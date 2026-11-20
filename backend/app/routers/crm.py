"""P11 — CRM & prospection : companies, contacts (import/dédup/score), opportunities
(pipeline Kanban), tasks, notes, segments (filtres sauvegardés), suppression list (RGPD).

Toutes les mutations exigent la permission `crm.manage` (Owner/Admin/Editor). La lecture
(liste/détail/pipeline/export) est ouverte à tout membre actif du tenant (Viewer inclus).

Dédup : contact.email normalisé (lowercase+strip), unique par tenant (uq_tenant_contact_email).
Import CSV/XLSX : skippe les doublons (existants + intra-batch) + les emails en suppression list.
Score : heuristique 0-100 (email, linkedin, titre senior, taille entreprise, téléphone).
"""

import csv
import io
from datetime import datetime, timedelta

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import exists, or_
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..pagination import Page, page_dep, set_total
from ..services import prospect_enrichment
from ..services.webhooks import emit_event

router = APIRouter(prefix="/api/tenants/{tid}/crm", tags=["crm"])

# ─── Constantes ──────────────────────────────────────────────────────────────
VALID_CONTACT_STATUS = {"new", "qualified", "contacted", "nurture", "won", "lost"}
VALID_OPP_STAGE = {"prospecting", "qualified", "proposal", "negotiation", "closed_won", "closed_lost"}
VALID_TASK_STATUS = {"todo", "in_progress", "done"}
VALID_TASK_PRIORITY = {"low", "medium", "high"}
VALID_SUPPRESSION_REASON = {"unsubscribe", "bounced", "complaint"}

PIPELINE_STAGES = [
    ("prospecting", "Prospection", "#94a3b8"),
    ("qualified", "Qualifié", "#0a66c2"),
    ("proposal", "Proposition", "#635bff"),
    ("negotiation", "Négociation", "#f59e0b"),
    ("closed_won", "Gagné", "#10b981"),
    ("closed_lost", "Perdu", "#ef4444"),
]

SENIOR_TITLE_KEYWORDS = {
    "ceo",
    "cto",
    "cfo",
    "coo",
    "cio",
    "vp",
    "vice president",
    "director",
    "head",
    "founder",
    "co-founder",
    "president",
    "chief",
    "gm",
    "managing director",
}
LARGE_COMPANY_SIZES = {"51-200", "201-500", "501-1000", "1000+"}


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _compute_score(
    c: models.Contact,
    company: models.Company | None = None,
    *,
    emails_sent: int = 0,
    last_contact_at: datetime | None = None,
    has_active_opportunity: bool = False,
) -> tuple[int, dict[str, int]]:
    """Heuristique 0-100 : profil P11 (5 critères 20 pts : email/linkedin/senior_title/
    company_size/phone, cap 100) + engagement P17 (emails envoyés +5/email cap 25, opportunité
    active +15, cap 40) - decay P17 (récence : -1/jour depuis dernier e-mail, cap 30). Total
    clampé 0-100. Sans engagement (défaut) → score = profil seul (backward compatible P11)."""
    breakdown = {}
    breakdown["email"] = 20 if c.email else 0
    breakdown["linkedin"] = 20 if (c.linkedin_url or "").strip() else 0
    title_low = (c.title or "").lower()
    breakdown["senior_title"] = 20 if any(k in title_low for k in SENIOR_TITLE_KEYWORDS) else 0
    size = (company.size if company else "") or ""
    breakdown["company_size"] = 20 if (size in LARGE_COMPANY_SIZES or size.endswith("+")) else 0
    breakdown["phone"] = 20 if (c.phone or "").strip() else 0
    profile = min(sum(breakdown.values()), 100)
    # P17 : engagement (emails envoyés + opportunité active).
    breakdown["engagement"] = min(emails_sent * 5, 25)
    breakdown["opportunity"] = 15 if has_active_opportunity else 0
    engagement = min(breakdown["engagement"] + breakdown["opportunity"], 40)
    # P17 : decay (récence dernier e-mail ; jamais contacté = pas de decay).
    decay = 0
    if last_contact_at is not None:
        decay = min(max((datetime.utcnow() - last_contact_at).days, 0), 30)
    breakdown["decay"] = -decay
    total = max(0, min(profile + engagement - decay, 100))
    return total, breakdown


def _engagement_signals(db: Session, c: models.Contact) -> tuple[int, datetime | None, bool]:
    """P17 : récupère les signals d'engagement depuis la DB — emails envoyés (Notification
    type=email status=sent, count — P24-A : dry_run exclu, simulé ≠ réel), date du dernier e-mail
    (last_contact_at pour le decay), opportunité active (Opportunity stage prospecting/qualified/
    proposal/negotiation). Réutilisé par _score_contact + recompute_score (DRY — un seul point)."""
    emails_sent = (
        db.query(models.Notification)
        .filter_by(tenant_id=c.tenant_id, recipient_contact_id=c.id, type="email")
        .filter(models.Notification.status == "sent")  # P24-A : exclure dry_run (simulé ≠ réel)
        .count()
    )
    last_notif = (
        db.query(models.Notification)
        .filter_by(tenant_id=c.tenant_id, recipient_contact_id=c.id, type="email")
        .filter(models.Notification.status == "sent")  # P24-A : exclure dry_run (simulé ≠ réel)
        .order_by(models.Notification.created_at.desc())
        .first()
    )
    last_contact_at = last_notif.created_at if last_notif else None
    has_opp = (
        db.query(models.Opportunity)
        .filter_by(tenant_id=c.tenant_id, contact_id=c.id)
        .filter(models.Opportunity.stage.in_(["prospecting", "qualified", "proposal", "negotiation"]))
        .first()
        is not None
    )
    return emails_sent, last_contact_at, has_opp


def _score_contact(db: Session, c: models.Contact) -> tuple[int, dict[str, int]]:
    """Calcule + applique le score d'un contact : profil P11 + engagement P17 (emails envoyés,
    opportunité active) - decay (récence dernier e-mail). Retourne (score, breakdown) pour que
    recompute_score puisse retourner le détail à l'API. Side-effect : c.score = score."""
    company = db.query(models.Company).filter_by(id=c.company_id).first() if c.company_id else None
    emails_sent, last_contact_at, has_opp = _engagement_signals(db, c)
    score, breakdown = _compute_score(
        c, company, emails_sent=emails_sent, last_contact_at=last_contact_at, has_active_opportunity=has_opp
    )
    c.score = score
    return score, breakdown


def _apply_contact_filters(
    q,
    tid: str,
    *,
    search=None,
    status=None,
    company_id=None,
    owner_id=None,
    score_min=None,
    source=None,
    has_email=None,
    no_email_days=None,
    enrolled_in_sequence=None,
    tag_id=None,
):
    """Applique les filtres search/status/company/owner/score/source + critères avancés P16
    (has_email, no_email_days, enrolled_in_sequence) + filtre par tag P22 (tag_id — EXISTS
    association ContactTag) à une query Contact.
    has_email (bool) : True = email réel (pas placeholder @linkedin.sync) ; False = sans email réel.
    no_email_days (int) : dormants — aucun e-mail envoyé (Notification type=email) dans les N derniers jours.
    enrolled_in_sequence (str) : contacts NON activement enrollés dans cette séquence (ciblage batch enroll)."""
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(
            or_(
                models.Contact.first_name.ilike(like),
                models.Contact.last_name.ilike(like),
                models.Contact.email.ilike(like),
                models.Contact.title.ilike(like),
            )
        )
    if status:
        q = q.filter(models.Contact.status == status)
    if company_id:
        q = q.filter(models.Contact.company_id == company_id)
    if owner_id:
        q = q.filter(models.Contact.owner_id == owner_id)
    if score_min is not None:
        q = q.filter(models.Contact.score >= score_min)
    if source:
        q = q.filter(models.Contact.source == source)
    # P16 : has_email — email réel (non null, non vide, non placeholder sync LinkedIn).
    if has_email is True:
        q = q.filter(
            models.Contact.email.isnot(None),
            models.Contact.email != "",
            ~models.Contact.email.like("%@linkedin.sync"),
            ~models.Contact.email.like("%@local.revpulse.io"),
        )
    elif has_email is False:
        q = q.filter(
            or_(
                models.Contact.email.is_(None),
                models.Contact.email == "",
                models.Contact.email.like("%@linkedin.sync"),
                models.Contact.email.like("%@local.revpulse.io"),
            )
        )
    # P16 : no_email_days — dormants (NOT EXISTS email dans les N derniers jours).
    if no_email_days is not None:
        cutoff = datetime.utcnow() - timedelta(days=no_email_days)
        q = q.filter(
            ~exists().where(
                models.Notification.recipient_contact_id == models.Contact.id,
                models.Notification.type == "email",
                models.Notification.created_at > cutoff,
            )
        )
    # P16 : enrolled_in_sequence — NON activement enrollés (ciblage batch enroll).
    if enrolled_in_sequence is not None:
        q = q.filter(
            ~exists().where(
                models.SequenceEnrollment.contact_id == models.Contact.id,
                models.SequenceEnrollment.sequence_id == enrolled_in_sequence,
                models.SequenceEnrollment.status == "active",
            )
        )
    # P22 : tag_id — EXISTS une association ContactTag pour ce tag (contact catégorisé par ce tag).
    if tag_id:
        q = q.filter(
            exists().where(
                models.ContactTag.contact_id == models.Contact.id,
                models.ContactTag.tag_id == tag_id,
            )
        )
    return q


def _attach_tags(db: Session, contacts: list[models.Contact]) -> list[models.Contact]:
    """P22 : batch-load les tags d'une liste de contacts (1 query IN sur ContactTag+Tag) + les
    attache via setattr (c.tags) pour que ContactOut.from_attributes les expose. DRY — appelé par
    list_contacts + get_contact. Pas de relationship ORM (pattern codebase : queries explicites)."""
    if not contacts:
        return contacts
    cids = [c.id for c in contacts]
    rows = (
        db.query(models.ContactTag.contact_id, models.Tag)
        .join(models.Tag, models.Tag.id == models.ContactTag.tag_id)
        .filter(models.ContactTag.contact_id.in_(cids))
        .all()
    )
    by_contact: dict[str, list[models.Tag]] = {}
    for cid, tag in rows:
        by_contact.setdefault(cid, []).append(tag)
    for c in contacts:
        c.tags = by_contact.get(c.id, [])
    return contacts


# ─── Companies ───────────────────────────────────────────────────────────────
@router.get("/companies", response_model=list[schemas.CompanyOut])
def list_companies(
    tid: str,
    response: Response,
    page: Page = Depends(page_dep),
    search: str | None = Query(None),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
):
    q = db.query(models.Company).filter_by(tenant_id=tid).order_by(models.Company.name)
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(or_(models.Company.name.ilike(like), models.Company.domain.ilike(like)))
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router.post("/companies", response_model=schemas.CompanyOut, status_code=status.HTTP_201_CREATED)
def create_company(
    tid: str,
    body: schemas.CompanyCreate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    c = models.Company(tenant_id=tid, **body.model_dump())
    db.add(c)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.company.create",
        target_type="company",
        target_id=c.id,
        detail={"name": body.name},
    )
    db.commit()
    db.refresh(c)
    return c


@router.get("/companies/{cid}", response_model=schemas.CompanyOut)
def get_company(tid: str, cid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    c = db.query(models.Company).filter_by(id=cid, tenant_id=tid).first()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entreprise introuvable")
    return c


@router.patch("/companies/{cid}", response_model=schemas.CompanyOut)
def update_company(
    tid: str,
    cid: str,
    body: schemas.CompanyCreate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    c = db.query(models.Company).filter_by(id=cid, tenant_id=tid).first()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entreprise introuvable")
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.company.update",
        target_type="company",
        target_id=cid,
        detail={"name": body.name},
    )
    db.commit()
    db.refresh(c)
    return c


@router.delete("/companies/{cid}", status_code=status.HTTP_200_OK)
def delete_company(
    tid: str,
    cid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    c = db.query(models.Company).filter_by(id=cid, tenant_id=tid).first()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entreprise introuvable")
    # Détache les contacts/opportunités (company_id nullable) avant suppression.
    db.query(models.Contact).filter_by(tenant_id=tid, company_id=cid).update({"company_id": None})
    db.query(models.Opportunity).filter_by(tenant_id=tid, company_id=cid).update({"company_id": None})
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.company.delete",
        target_type="company",
        target_id=cid,
        detail={"name": c.name},
    )
    db.delete(c)
    db.commit()
    return {"deleted": cid}


# ─── Contacts ────────────────────────────────────────────────────────────────
@router.get("/contacts", response_model=list[schemas.ContactOut])
def list_contacts(
    tid: str,
    response: Response,
    page: Page = Depends(page_dep),
    search: str | None = Query(None),
    status: str | None = Query(None),
    company_id: str | None = Query(None),
    owner_id: str | None = Query(None),
    score_min: int | None = Query(None, ge=0, le=100),
    source: str | None = Query(None),
    has_email: bool | None = Query(None),
    no_email_days: int | None = Query(None, ge=0),
    enrolled_in_sequence: str | None = Query(None),
    tag_id: str | None = Query(None, description="P22 : filtrer les contacts ayant ce tag (EXISTS ContactTag)"),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
):
    q = db.query(models.Contact).filter_by(tenant_id=tid)
    q = _apply_contact_filters(
        q,
        tid,
        search=search,
        status=status,
        company_id=company_id,
        owner_id=owner_id,
        score_min=score_min,
        source=source,
        has_email=has_email,
        no_email_days=no_email_days,
        enrolled_in_sequence=enrolled_in_sequence,
        tag_id=tag_id,
    )
    q = q.order_by(models.Contact.created_at.desc())
    set_total(response, q.count())
    return _attach_tags(db, q.offset(page.skip).limit(page.limit).all())


@router.post("/contacts", response_model=schemas.ContactOut, status_code=status.HTTP_201_CREATED)
def create_contact(
    tid: str,
    body: schemas.ContactCreate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    if body.status not in VALID_CONTACT_STATUS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Statut invalide : {body.status}")
    email = _normalize_email(body.email)
    if db.query(models.Contact).filter_by(tenant_id=tid, email=email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Cet e-mail existe déjà pour ce tenant (dédup).")
    c = models.Contact(
        tenant_id=tid,
        company_id=body.company_id,
        first_name=body.first_name,
        last_name=body.last_name,
        email=email,
        phone=body.phone,
        title=body.title,
        linkedin_url=body.linkedin_url,
        status=body.status,
        owner_id=body.owner_id,
        source=body.source,
    )
    _score_contact(db, c)
    db.add(c)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.contact.create",
        target_type="contact",
        target_id=c.id,
        detail={"email": email, "score": c.score},
    )
    emit_event(
        db,
        tenant_id=tid,
        event="contact.created",
        payload={
            "id": c.id,
            "tenant_id": tid,
            "email": email,
            "first_name": c.first_name,
            "last_name": c.last_name,
            "title": c.title,
            "score": c.score,
            "source": c.source,
            "owner_id": c.owner_id,
        },
    )
    db.commit()
    db.refresh(c)
    return c


# ─── P29-V2 — Enrichissement de prospects (Hunter.io Email Finder) ──────────
def _extract_domain(website: str) -> str:
    """Extrait le domaine d'une URL website (fallback si company.domain vide).
    'https://www.example.com/page' → 'example.com'. Simple (pas de validation eTLD)."""
    if not website:
        return ""
    w = website.strip().lower()
    for prefix in ("https://", "http://", "www."):
        if w.startswith(prefix):
            w = w[len(prefix):]
    return w.split("/")[0].strip()


@router.post("/contacts/{cid}/enrich")
def enrich_contact(
    tid: str,
    cid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Enrichit un prospect via Hunter.io Email Finder (P29-V2). Le commercial déclenche l'enrichment
    explicite — le contact doit avoir une entreprise (company) avec un domaine (ou website).

    - enriched → 200, contact mis à jour (email, title, linkedin_url, score recompute).
    - not_configured → 503 (HUNTER_API_KEY absent — simulé, AUCUN email inventé).
    - not_found → 422 (API appelée, aucun email fiable — confidence < seuil).
    - error → 400 (échec technique / rate-limit).
    """
    c = db.query(models.Contact).filter_by(id=cid, tenant_id=tid).first()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable.")
    # Récupérer le domaine (company.domain OU extrait de company.website).
    company = db.query(models.Company).filter_by(id=c.company_id).first() if c.company_id else None
    domain = ""
    if company:
        domain = (company.domain or "").strip().lower() or _extract_domain(company.website or "")
    if not domain:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Le contact doit avoir une entreprise avec un domaine (ou website) pour l'enrichment.")
    if not c.first_name or not c.last_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Le contact doit avoir first_name + last_name pour l'enrichment.")

    result = prospect_enrichment.enrich_prospect(domain, c.first_name, c.last_name, db, tid)

    if result.status == "error":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, result.error or "Échec enrichment.")
    if result.status == "not_configured":
        # Simulé : on log l'audit mais on ne modifie pas le contact (anti-fake : AUCUN email inventé).
        log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
                  action="crm.contact.enrich", target_type="contact", target_id=c.id,
                  detail={"status": "not_configured", "simulated": True, "domain": domain})
        db.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            result.error or "Hunter.io non configuré (HUNTER_API_KEY absent).")
    if result.status == "not_found":
        log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
                  action="crm.contact.enrich", target_type="contact", target_id=c.id,
                  detail={"status": "not_found", "confidence": result.confidence, "domain": domain})
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            result.error or "Aucun email fiable trouvé (confidence insuffisante).")

    # enriched → mettre à jour le contact (email, title, linkedin_url) + recompute score.
    old_email = c.email
    if result.email:
        # Dédup : l'email ne doit pas appartenir à un autre contact du tenant.
        existing = db.query(models.Contact).filter_by(tenant_id=tid, email=result.email).first()
        if existing and existing.id != c.id:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Email enrichi déjà utilisé par le contact {existing.id}.")
        c.email = result.email
    if result.position:
        c.title = result.position
    if result.linkedin_url and not c.linkedin_url:
        c.linkedin_url = result.linkedin_url
    score, breakdown = _score_contact(db, c)  # recompute score post-enrichment
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="crm.contact.enrich", target_type="contact", target_id=c.id,
              detail={"status": "enriched", "email": result.email, "confidence": result.confidence,
                      "old_email": old_email, "position": result.position,
                      "domain": domain, "score": score})
    emit_event(db, tenant_id=tid, event="contact.enriched",
               payload={"contact_id": c.id, "email": result.email,
                        "confidence": result.confidence, "position": result.position})
    db.commit()
    db.refresh(c)
    return {"status": "enriched", "contact_id": c.id, "email": result.email,
            "confidence": result.confidence, "position": result.position,
            "company": result.company, "score": score, "score_breakdown": breakdown,
            "provider": "hunter.io"}


# Colonnes partagées entre export CSV et XLSX (source unique de vérité — P85).
_CRM_EXPORT_COLS = ["id", "first_name", "last_name", "email", "phone", "title",
                    "status", "score", "linkedin_url", "company_id", "owner_id",
                    "source", "created_at"]


def _crm_contact_row(c):
    """Ligne normalisée pour export CRM (CSV + XLSX). Évite la duplication de logique."""
    return [c.id, c.first_name, c.last_name, c.email, c.phone, c.title,
            c.status, c.score, c.linkedin_url,
            c.company_id or "", c.owner_id or "", c.source,
            c.created_at.isoformat() if c.created_at else ""]


@router.get("/contacts/export")
def export_contacts(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Export CSV de tous les contacts du tenant (filtres optionnels via query string).
    RGPD portabilité + export commercial. Lecture ouverte à tout membre actif (docstring module)."""
    q = db.query(models.Contact).filter_by(tenant_id=tid).order_by(models.Contact.created_at.desc())
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CRM_EXPORT_COLS)
    for c in q.all():
        writer.writerow(_crm_contact_row(c))
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=revpulse_contacts_{tid}.csv"},
    )


@router.get("/contacts/export.xlsx")
def export_contacts_xlsx(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Export Excel (.xlsx) de tous les contacts du tenant (P85). Même RBAC que le CSV
    (lecture ouverte à tout membre actif — docstring module), mêmes 13 colonnes, même tri.
    Format : en-tête gras sur fond bleu, freeze top row, autofilter, largeurs auto.
    Utilise openpyxl (déjà en requirements, déjà utilisé côté import _read_rows)."""
    q = db.query(models.Contact).filter_by(tenant_id=tid).order_by(models.Contact.created_at.desc())
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Contacts"
    ws.append(_CRM_EXPORT_COLS)
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center")
    for col_idx in range(1, len(_CRM_EXPORT_COLS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
    for c in q.all():
        ws.append(_crm_contact_row(c))
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for col_idx, col_name in enumerate(_CRM_EXPORT_COLS, start=1):
        width = max(12, len(col_name) + 2)
        if col_name in ("email", "linkedin_url", "created_at"):
            width = max(width, 28)
        elif col_name in ("first_name", "last_name", "title", "source", "company_id", "owner_id"):
            width = max(width, 18)
        ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = width
    buf = io.BytesIO()
    wb.save(buf)
    xlsx_data = buf.getvalue()
    return Response(
        content=xlsx_data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="revpulse_contacts_{tid}.xlsx"',
                 "Content-Length": str(len(xlsx_data))})


# --- Import CSV/XLSX + dédup + suppression ---
def _read_rows(file: UploadFile) -> list[dict]:
    """Parse un fichier CSV ou XLSX en liste de dicts {col: value}. Détection par extension."""
    name = (file.filename or "").lower()
    raw = file.file.read()
    if name.endswith(".xlsx"):
        try:
            from openpyxl import load_workbook
        except ImportError as e:
            raise HTTPException(
                status.HTTP_501_NOT_IMPLEMENTED, "Import XLSX non disponible (openpyxl absent). Utilisez CSV."
            ) from e
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h or "").strip().lower() for h in rows[0]]
        out = []
        for r in rows[1:]:
            out.append(
                {headers[i]: ("" if r[i] is None else str(r[i]).strip()) for i in range(min(len(headers), len(r)))}
            )
        return out
    # défaut CSV (utf-8-sig pour BOM Excel)
    text = raw.decode("utf-8-sig", errors="replace")
    return [dict(r) for r in csv.DictReader(io.StringIO(text))]


@router.post("/contacts/import", response_model=schemas.ImportResult)
def import_contacts(
    tid: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Import CSV/XLSX de contacts. Pour chaque ligne :
    - normalise l'email (lowercase+strip), valide (EmailStr via parse)
    - skip si email dans suppression_list (RGPD) → suppressed++
    - skip si contact existant (même email) OU doublon intra-batch → duplicates++
    - crée sinon (score calculé, source=import) → created++
    - erreur si email absent/invalide → errors[]
    """
    rows = _read_rows(file)
    result = schemas.ImportResult(total=len(rows))
    seen: set[str] = set()
    # Précharge les emails existants + supprimés du tenant (évite N queries).
    existing = {
        c.email for c in db.query(models.Contact).filter_by(tenant_id=tid).with_entities(models.Contact.email).all()
    }
    suppressed = {
        s.email
        for s in db.query(models.SuppressionList)
        .filter_by(tenant_id=tid)
        .with_entities(models.SuppressionList.email)
        .all()
    }
    for i, row in enumerate(rows, start=2):  # ligne 1 = header (CSV) ou 1 (XLSX)
        raw_email = (row.get("email") or row.get("e-mail") or row.get("courriel") or "").strip()
        if not raw_email:
            result.errors.append(schemas.ImportRowError(row=i, error="e-mail manquant"))
            continue
        email = _normalize_email(raw_email)
        # Validation basique (format email) — évite un import d'email pourri.
        if "@" not in email or "." not in email.split("@")[-1]:
            result.errors.append(schemas.ImportRowError(row=i, email=email, error="e-mail invalide"))
            continue
        if email in suppressed:
            result.suppressed += 1
            continue
        if email in existing or email in seen:
            result.duplicates += 1
            continue
        seen.add(email)
        # Company optionnelle : lookup par nom, sinon création à la volée.
        company_id = None
        comp_name = (row.get("company") or row.get("entreprise") or "").strip()
        if comp_name:
            comp = db.query(models.Company).filter_by(tenant_id=tid, name=comp_name).first()
            if not comp:
                comp = models.Company(tenant_id=tid, name=comp_name)
                db.add(comp)
                db.flush()
            company_id = comp.id
        c = models.Contact(
            tenant_id=tid,
            company_id=company_id,
            first_name=(row.get("first_name") or row.get("firstname") or row.get("prénom") or "").strip(),
            last_name=(row.get("last_name") or row.get("lastname") or row.get("nom") or "").strip(),
            email=email,
            phone=(row.get("phone") or row.get("téléphone") or "").strip(),
            title=(row.get("title") or row.get("titre") or row.get("fonction") or "").strip(),
            linkedin_url=(row.get("linkedin_url") or row.get("linkedin") or "").strip(),
            status=row.get("status", "new").strip().lower() or "new",
            source="import",
        )
        if c.status not in VALID_CONTACT_STATUS:
            c.status = "new"
        _score_contact(db, c)
        db.add(c)
        result.created += 1
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.contact.import",
        target_type="contact",
        detail={
            "created": result.created,
            "duplicates": result.duplicates,
            "suppressed": result.suppressed,
            "errors": len(result.errors),
            "total": result.total,
        },
    )
    db.commit()
    return result


@router.get("/contacts/{ctid}", response_model=schemas.ContactOut)
def get_contact(tid: str, ctid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    c = db.query(models.Contact).filter_by(id=ctid, tenant_id=tid).first()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable")
    return _attach_tags(db, [c])[0]  # P22 : attache les tags du contact pour ContactOut


@router.patch("/contacts/{ctid}", response_model=schemas.ContactOut)
def update_contact(
    tid: str,
    ctid: str,
    body: schemas.ContactUpdate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    c = db.query(models.Contact).filter_by(id=ctid, tenant_id=tid).first()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable")
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in VALID_CONTACT_STATUS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Statut invalide : {data['status']}")
    if "email" in data and data["email"] is not None:
        new_email = _normalize_email(data["email"])
        if new_email != c.email:
            if db.query(models.Contact).filter_by(tenant_id=tid, email=new_email).first():
                raise HTTPException(status.HTTP_409_CONFLICT, "Cet e-mail existe déjà (dédup).")
            data["email"] = new_email
    for k, v in data.items():
        setattr(c, k, v)
    c.updated_at = datetime.utcnow()
    _score_contact(db, c)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.contact.update",
        target_type="contact",
        target_id=ctid,
        detail={k: v for k, v in data.items() if k != "email"} | {"score": c.score},
    )
    db.commit()
    db.refresh(c)
    return c


@router.delete("/contacts/{ctid}", status_code=status.HTTP_200_OK)
def delete_contact(
    tid: str,
    ctid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Supprime un contact + cascade ses opportunités/tâches/notes (RGPD droit à l'oubli).
    L'audit log survit (tenant_id sans FK)."""
    c = db.query(models.Contact).filter_by(id=ctid, tenant_id=tid).first()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable")
    # Cascade manuelle : opps → tasks/notes liés, puis tasks/notes du contact.
    opp_ids = [o.id for o in db.query(models.Opportunity).filter_by(tenant_id=tid, contact_id=ctid).all()]
    if opp_ids:
        db.query(models.Task).filter(models.Task.tenant_id == tid, models.Task.opportunity_id.in_(opp_ids)).delete(
            synchronize_session=False
        )
        db.query(models.Note).filter(models.Note.tenant_id == tid, models.Note.opportunity_id.in_(opp_ids)).delete(
            synchronize_session=False
        )
        db.query(models.Opportunity).filter_by(tenant_id=tid, contact_id=ctid).delete(synchronize_session=False)
    db.query(models.Task).filter_by(tenant_id=tid, contact_id=ctid).delete(synchronize_session=False)
    db.query(models.Note).filter_by(tenant_id=tid, contact_id=ctid).delete(synchronize_session=False)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.contact.delete",
        target_type="contact",
        target_id=ctid,
        detail={"email": c.email},
    )
    db.delete(c)
    db.commit()
    return {"deleted": ctid}


@router.post("/contacts/{ctid}/score", response_model=schemas.ScoreBreakdown)
def recompute_score(
    tid: str,
    ctid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Recalcule le score d'un contact (profil P11 + engagement P17 + decay) + retourne le détail."""
    c = db.query(models.Contact).filter_by(id=ctid, tenant_id=tid).first()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable")
    score, breakdown = _score_contact(db, c)
    c.updated_at = datetime.utcnow()
    emit_event(
        db,
        tenant_id=tid,
        event="contact.scored",
        payload={"id": c.id, "tenant_id": tid, "email": c.email, "score": score, "breakdown": breakdown},
    )
    db.commit()
    return schemas.ScoreBreakdown(score=score, breakdown=breakdown)


@router.post("/contacts/score-recompute", response_model=schemas.ScoreRecomputeResult)
def recompute_all_scores(
    tid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Batch recompute (P17) : recalcule le score de tous les contacts du tenant (profil P11
    + engagement P17 emails/opportunité + decay récence). Utile après import massif ou pour
    rafraîchir le decay. Retourne {recomputed, avg_score}. Audit crm.score_recompute_batch."""
    contacts = db.query(models.Contact).filter_by(tenant_id=tid).all()
    for c in contacts:
        _score_contact(db, c)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.score_recompute_batch",
        target_type="tenant",
        target_id=tid,
        detail={"recomputed": len(contacts)},
    )
    db.commit()
    avg = sum(c.score for c in contacts) / len(contacts) if contacts else 0
    return schemas.ScoreRecomputeResult(recomputed=len(contacts), avg_score=round(avg, 1))


# ─── Opportunities + pipeline Kanban ─────────────────────────────────────────
@router.get("/opportunities", response_model=list[schemas.OpportunityOut])
def list_opportunities(
    tid: str,
    response: Response,
    page: Page = Depends(page_dep),
    stage: str | None = Query(None),
    contact_id: str | None = Query(None),
    owner_id: str | None = Query(None),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
):
    q = db.query(models.Opportunity).filter_by(tenant_id=tid)
    if stage:
        q = q.filter(models.Opportunity.stage == stage)
    if contact_id:
        q = q.filter(models.Opportunity.contact_id == contact_id)
    if owner_id:
        q = q.filter(models.Opportunity.owner_id == owner_id)
    q = q.order_by(models.Opportunity.updated_at.desc())
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router.post("/opportunities", response_model=schemas.OpportunityOut, status_code=status.HTTP_201_CREATED)
def create_opportunity(
    tid: str,
    body: schemas.OpportunityCreate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    if body.stage not in VALID_OPP_STAGE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Stage invalide : {body.stage}")
    # Vérifie que le contact existe + appartient au tenant.
    if not db.query(models.Contact).filter_by(id=body.contact_id, tenant_id=tid).first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable pour ce tenant")
    o = models.Opportunity(
        tenant_id=tid,
        contact_id=body.contact_id,
        company_id=body.company_id,
        title=body.title,
        value=body.value,
        currency=body.currency,
        stage=body.stage,
        probability=body.probability,
        expected_close_date=body.expected_close_date,
        owner_id=body.owner_id,
    )
    db.add(o)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.opportunity.create",
        target_type="opportunity",
        target_id=o.id,
        detail={"title": body.title, "value": body.value, "stage": body.stage},
    )
    emit_event(
        db,
        tenant_id=tid,
        event="opportunity.created",
        payload={
            "id": o.id,
            "tenant_id": tid,
            "contact_id": o.contact_id,
            "title": o.title,
            "value": o.value,
            "currency": o.currency,
            "stage": o.stage,
            "probability": o.probability,
        },
    )
    db.commit()
    db.refresh(o)
    return o


@router.get("/opportunities/pipeline", response_model=list[schemas.PipelineColumn])
def get_pipeline(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Vue Kanban : opportunités groupées par stage (ordre canonique du pipeline)."""
    opps = db.query(models.Opportunity).filter_by(tenant_id=tid).all()
    by_stage: dict[str, list] = {s: [] for s, _, _ in PIPELINE_STAGES}
    for o in opps:
        by_stage.setdefault(o.stage, []).append(o)
    cols = []
    for stage, label, color in PIPELINE_STAGES:
        items = by_stage.get(stage, [])
        cols.append(
            schemas.PipelineColumn(
                stage=stage,
                label=label,
                color=color,
                opportunities=items,
                count=len(items),
                total_value=sum(o.value for o in items),
            )
        )
    return cols


@router.get("/opportunities/{oid}", response_model=schemas.OpportunityOut)
def get_opportunity(tid: str, oid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    o = db.query(models.Opportunity).filter_by(id=oid, tenant_id=tid).first()
    if not o:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunité introuvable")
    return o


@router.patch("/opportunities/{oid}", response_model=schemas.OpportunityOut)
def update_opportunity(
    tid: str,
    oid: str,
    body: schemas.OpportunityUpdate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    o = db.query(models.Opportunity).filter_by(id=oid, tenant_id=tid).first()
    if not o:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunité introuvable")
    data = body.model_dump(exclude_unset=True)
    if "stage" in data and data["stage"] not in VALID_OPP_STAGE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Stage invalide : {data['stage']}")
    # P24 : transition stage→closed_won → auto-set closed_at (time-to-close) ; retour arrière → clear.
    if data.get("stage") == "closed_won" and o.stage != "closed_won":
        o.closed_at = datetime.utcnow()
    elif data.get("stage") and data.get("stage") != "closed_won" and o.stage == "closed_won":
        o.closed_at = None
    for k, v in data.items():
        setattr(o, k, v)
    o.updated_at = datetime.utcnow()
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.opportunity.update",
        target_type="opportunity",
        target_id=oid,
        detail=data,
    )
    db.commit()
    db.refresh(o)
    return o


@router.patch("/opportunities/{oid}/stage", response_model=schemas.OpportunityOut)
def move_opportunity(
    tid: str,
    oid: str,
    body: schemas.StageUpdate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Déplacement Kanban (drag-and-drop) : change uniquement le stage."""
    if body.stage not in VALID_OPP_STAGE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Stage invalide : {body.stage}")
    o = db.query(models.Opportunity).filter_by(id=oid, tenant_id=tid).first()
    if not o:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunité introuvable")
    old = o.stage
    # P24 : transition stage→closed_won → auto-set closed_at (time-to-close) ; retour arrière → clear.
    if body.stage == "closed_won" and old != "closed_won":
        o.closed_at = datetime.utcnow()
    elif body.stage != "closed_won" and old == "closed_won":
        o.closed_at = None
    o.stage = body.stage
    o.updated_at = datetime.utcnow()
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.opportunity.move",
        target_type="opportunity",
        target_id=oid,
        detail={"old": old, "new": body.stage},
    )
    db.commit()
    db.refresh(o)
    return o


@router.delete("/opportunities/{oid}", status_code=status.HTTP_200_OK)
def delete_opportunity(
    tid: str,
    oid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    o = db.query(models.Opportunity).filter_by(id=oid, tenant_id=tid).first()
    if not o:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunité introuvable")
    # Cascade tasks/notes liées à l'opportunité.
    db.query(models.Task).filter_by(tenant_id=tid, opportunity_id=oid).delete(synchronize_session=False)
    db.query(models.Note).filter_by(tenant_id=tid, opportunity_id=oid).delete(synchronize_session=False)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.opportunity.delete",
        target_type="opportunity",
        target_id=oid,
        detail={"title": o.title},
    )
    db.delete(o)
    db.commit()
    return {"deleted": oid}


# ─── Tasks ───────────────────────────────────────────────────────────────────
@router.get("/tasks", response_model=list[schemas.TaskOut])
def list_tasks(
    tid: str,
    response: Response,
    page: Page = Depends(page_dep),
    status: str | None = Query(None),
    contact_id: str | None = Query(None),
    opportunity_id: str | None = Query(None),
    assignee_id: str | None = Query(None),
    priority: str | None = Query(None),
    overdue: bool | None = Query(None, description="P23 : due_date < today AND status != done"),
    due_today: bool | None = Query(None, description="P23 : due_date == today AND status != done"),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
):
    q = db.query(models.Task).filter_by(tenant_id=tid)
    if status:
        q = q.filter(models.Task.status == status)
    if contact_id:
        q = q.filter(models.Task.contact_id == contact_id)
    if opportunity_id:
        q = q.filter(models.Task.opportunity_id == opportunity_id)
    if assignee_id:
        q = q.filter(models.Task.assignee_id == assignee_id)
    if priority:
        q = q.filter(models.Task.priority == priority)
    today = datetime.utcnow().date()
    if overdue is True:
        q = q.filter(
            models.Task.due_date.isnot(None),
            models.Task.due_date < today,
            models.Task.status != "done",
        )
    if due_today is True:
        q = q.filter(models.Task.due_date == today, models.Task.status != "done")
    q = q.order_by(models.Task.due_date.asc().nullslast(), models.Task.created_at.desc())
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router.post("/tasks", response_model=schemas.TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    tid: str,
    body: schemas.TaskCreate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    if body.status not in VALID_TASK_STATUS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Statut invalide : {body.status}")
    if body.priority not in VALID_TASK_PRIORITY:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Priorité invalide : {body.priority}")
    t = models.Task(
        tenant_id=tid,
        contact_id=body.contact_id,
        opportunity_id=body.opportunity_id,
        title=body.title,
        due_date=body.due_date,
        status=body.status,
        priority=body.priority,
        assignee_id=body.assignee_id,
        reminder_at=body.reminder_at,
    )
    db.add(t)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.task.create",
        target_type="task",
        target_id=t.id,
        detail={"title": body.title},
    )
    db.commit()
    db.refresh(t)
    return t


@router.get("/tasks/{tkid}", response_model=schemas.TaskOut)
def get_task(tid: str, tkid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    t = db.query(models.Task).filter_by(id=tkid, tenant_id=tid).first()
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tâche introuvable")
    return t


@router.patch("/tasks/{tkid}", response_model=schemas.TaskOut)
def update_task(
    tid: str,
    tkid: str,
    body: schemas.TaskUpdate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    t = db.query(models.Task).filter_by(id=tkid, tenant_id=tid).first()
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tâche introuvable")
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in VALID_TASK_STATUS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Statut invalide : {data['status']}")
    if "priority" in data and data["priority"] not in VALID_TASK_PRIORITY:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Priorité invalide : {data['priority']}")
    # P23 : reminder_at changé → reset reminder_sent (le nouveau rappel doit pouvoir partir).
    if "reminder_at" in data and data["reminder_at"] != t.reminder_at:
        data["reminder_sent"] = False
    # P23 : transition status→done → auto-set completed_at ; retour arrière (done→autre) → clear.
    if data.get("status") == "done" and t.status != "done":
        data["completed_at"] = datetime.utcnow()
    elif data.get("status") and data.get("status") != "done" and t.status == "done":
        data["completed_at"] = None
    for k, v in data.items():
        setattr(t, k, v)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.task.update",
        target_type="task",
        target_id=tkid,
        detail=data,
    )
    db.commit()
    db.refresh(t)
    return t


@router.delete("/tasks/{tkid}", status_code=status.HTTP_200_OK)
def delete_task(
    tid: str,
    tkid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    t = db.query(models.Task).filter_by(id=tkid, tenant_id=tid).first()
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tâche introuvable")
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.task.delete",
        target_type="task",
        target_id=tkid,
        detail={"title": t.title},
    )
    db.delete(t)
    db.commit()
    return {"deleted": tkid}


@router.post("/tasks/process-reminders", response_model=dict)
def process_task_reminders(
    tid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("workspace.manage")),
):
    """P23 : déclenche manuellement le scheduler de rappels de tâches (admin Owner/Admin). Utile pour
    tests + reprise après downtime. Tourne normalement en background (start_scheduler → process_due_task_reminders
    toutes les SEQUENCE_SCHEDULER_INTERVAL_SECONDS). Retourne {sent, skipped_no_assignee, processed}."""
    from ..services.task_reminders import process_due_task_reminders

    result = process_due_task_reminders(db)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="task.reminders_trigger",
        target_type="task",
        detail=result,
    )
    db.commit()
    return result


# ─── Notes ───────────────────────────────────────────────────────────────────
@router.get("/notes", response_model=list[schemas.NoteOut])
def list_notes(
    tid: str,
    response: Response,
    page: Page = Depends(page_dep),
    contact_id: str | None = Query(None),
    opportunity_id: str | None = Query(None),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
):
    q = db.query(models.Note).filter_by(tenant_id=tid)
    if contact_id:
        q = q.filter(models.Note.contact_id == contact_id)
    if opportunity_id:
        q = q.filter(models.Note.opportunity_id == opportunity_id)
    q = q.order_by(models.Note.created_at.desc())
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router.post("/notes", response_model=schemas.NoteOut, status_code=status.HTTP_201_CREATED)
def create_note(
    tid: str,
    body: schemas.NoteCreate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    if not body.contact_id and not body.opportunity_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Une note doit être liée à un contact ou une opportunité.")
    n = models.Note(
        tenant_id=tid,
        contact_id=body.contact_id,
        opportunity_id=body.opportunity_id,
        body=body.body,
        author_id=membership.user_id,
    )
    db.add(n)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.note.create",
        target_type="note",
        target_id=n.id,
        detail={"contact_id": body.contact_id, "opportunity_id": body.opportunity_id},
    )
    db.commit()
    db.refresh(n)
    return n


@router.delete("/notes/{nid}", status_code=status.HTTP_200_OK)
def delete_note(
    tid: str,
    nid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    n = db.query(models.Note).filter_by(id=nid, tenant_id=tid).first()
    if not n:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note introuvable")
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.note.delete",
        target_type="note",
        target_id=nid,
    )
    db.delete(n)
    db.commit()
    return {"deleted": nid}


# ─── Segments (filtres sauvegardés) ──────────────────────────────────────────
@router.get("/segments", response_model=list[schemas.SegmentOut])
def list_segments(
    tid: str,
    response: Response,
    page: Page = Depends(page_dep),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
):
    q = db.query(models.Segment).filter_by(tenant_id=tid).order_by(models.Segment.created_at.desc())
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router.post("/segments", response_model=schemas.SegmentOut, status_code=status.HTTP_201_CREATED)
def create_segment(
    tid: str,
    body: schemas.SegmentCreate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    s = models.Segment(tenant_id=tid, name=body.name, criteria=body.criteria)
    db.add(s)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.segment.create",
        target_type="segment",
        target_id=s.id,
        detail={"name": body.name},
    )
    db.commit()
    db.refresh(s)
    return s


@router.get("/segments/{sid}/contacts", response_model=list[schemas.ContactOut])
def apply_segment(
    tid: str,
    sid: str,
    response: Response,
    page: Page = Depends(page_dep),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
):
    """Applique le critère du segment + retourne les contacts matchés (paginé)."""
    s = db.query(models.Segment).filter_by(id=sid, tenant_id=tid).first()
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment introuvable")
    criteria = s.criteria or {}
    q = db.query(models.Contact).filter_by(tenant_id=tid)
    q = _apply_contact_filters(
        q,
        tid,
        search=criteria.get("search"),
        status=criteria.get("status"),
        company_id=criteria.get("company_id"),
        owner_id=criteria.get("owner_id"),
        score_min=criteria.get("score_min"),
        source=criteria.get("source"),
        has_email=criteria.get("has_email"),
        no_email_days=criteria.get("no_email_days"),
        enrolled_in_sequence=criteria.get("enrolled_in_sequence"),
    )
    q = q.order_by(models.Contact.created_at.desc())
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router.delete("/segments/{sid}", status_code=status.HTTP_200_OK)
def delete_segment(
    tid: str,
    sid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    s = db.query(models.Segment).filter_by(id=sid, tenant_id=tid).first()
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment introuvable")
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.segment.delete",
        target_type="segment",
        target_id=sid,
        detail={"name": s.name},
    )
    db.delete(s)
    db.commit()
    return {"deleted": sid}


# ─── Suppression list (RGPD — désinscription/bounce/réclamation) ──────────────
@router.get("/suppression", response_model=list[schemas.SuppressionOut])
def list_suppression(
    tid: str,
    response: Response,
    page: Page = Depends(page_dep),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
):
    q = db.query(models.SuppressionList).filter_by(tenant_id=tid).order_by(models.SuppressionList.created_at.desc())
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router.post("/suppression", response_model=schemas.SuppressionOut, status_code=status.HTTP_201_CREATED)
def add_suppression(
    tid: str,
    body: schemas.SuppressionCreate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    if body.reason not in VALID_SUPPRESSION_REASON:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Raison invalide : {body.reason}")
    email = _normalize_email(body.email)
    existing = db.query(models.SuppressionList).filter_by(tenant_id=tid, email=email).first()
    if existing:
        # Idempotent : met à jour la raison si déjà présent.
        existing.reason = body.reason
        existing.source = body.source
        db.commit()
        db.refresh(existing)
        return existing
    s = models.SuppressionList(tenant_id=tid, email=email, reason=body.reason, source=body.source)
    db.add(s)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.suppression.add",
        target_type="suppression",
        target_id=s.id,
        detail={"email": email, "reason": body.reason},
    )
    db.commit()
    db.refresh(s)
    return s


@router.delete("/suppression/{sid}", status_code=status.HTTP_200_OK)
def remove_suppression(
    tid: str,
    sid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    s = db.query(models.SuppressionList).filter_by(id=sid, tenant_id=tid).first()
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entrée de suppression introuvable")
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="crm.suppression.remove",
        target_type="suppression",
        target_id=sid,
        detail={"email": s.email},
    )
    db.delete(s)
    db.commit()
    return {"deleted": sid}
