"""GDPR (RGPD) — right to data portability (Art. 20) + right to erasure / right-to-be-forgotten (Art. 17).

Endpoints (préfixe /api/tenants/{tid}/gdpr) :
  GET  /export?user_id=   — bundle JSON des données personnelles d'un membre (User + Member + Contacts
                             détenus + AuditLog entries déclenchées + CareerProfile + JobApplications).
                             Droit à la portabilité (GDPR Art. 20).
  DELETE /erase?user_id=  — anonymise le membre (User.name/email, Member.name/email, Contacts.first_name/
                             last_name/email/phone/linkedin_url, AuditLog.actor_name scrubbed). Marque
                             User.gdpr_erased_at = now (preuve de conformité, soft-delete audit trail).
                             Droit à l'effacement (GDPR Art. 17).

RBAC : workspace.manage (Owner) — seul le propriétaire de l'espace exécute les droits GDPR.
Protection : impossible d'effacer soi-même (_perm.user_id == user_id → 400) ou le Owner du tenant
(member.role == "Owner" → 400, verrou anti-lockout). Les enregistrements structurels sont CONSERVÉS
(anonymisés) pour l'intégrité FK + la rétention audit SOC2 (7 ans) — GDPR Art. 17.3.b/e (obligations
légales de conservation priment sur le droit à l'effacement pour les logs d'audit).
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..audit import log_audit
from ..database import get_db
from ..deps import get_membership, get_tenant, require_permission
from ..logging_config import request_ip_var, request_ua_var

router = APIRouter(prefix="/api/tenants/{tid}/gdpr", tags=["gdpr"])

# Purposes valides pour le consent (Art. 7 — consentement explicite par finalité).
_CONSENT_PURPOSES = {"marketing", "analytics", "third_party_sharing"}
_DEFAULT_PP_VERSION = "2026-11-18-v1"


class ConsentGrant(BaseModel):
    """Body POST /gdpr/consent : grant consent pour un purpose (Art. 7)."""
    purpose: str
    privacy_policy_version: str = _DEFAULT_PP_VERSION


def _erased_email(seed: str) -> str:
    """Email anonymisé préservant la contrainte UNIQUE (User.email, Member.email, Contact.email).
    Forme : erased+<seed>@revpulse.local — ne fuite aucune donnée réelle."""
    return f"erased+{seed}@revpulse.local"


@router.get("/export")
def gdpr_export(
    tid: str,
    user_id: str = Query(..., description="ID du membre dont on exporte les données (GDPR Art. 20)"),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """GET /gdpr/export : bundle JSON des données personnelles d'un membre (portabilité GDPR Art. 20).
    Inclut : User, Member, Contacts détenus (owner_id), AuditLog entries (actor_user_id),
    CareerProfile, JobApplications (via career_profile.user_id). RBAC workspace.manage."""
    # Le user_id doit être un member de CE tenant (isolation multi-tenant)
    member = db.query(models.Member).filter_by(tenant_id=tid, user_id=user_id).first()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Aucun membre avec cet user_id dans ce tenant")
    user = db.query(models.User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User introuvable")

    contacts = db.query(models.Contact).filter_by(owner_id=user_id).all()
    audit_entries = db.query(models.AuditLog).filter_by(tenant_id=tid, actor_user_id=user_id).all()
    career = db.query(models.CareerProfile).filter_by(user_id=user_id).first()
    job_apps = []
    if career:
        job_apps = db.query(models.JobApplication).filter_by(career_profile_id=career.id).all()
    # P65 (P0-G2) — Art. 20 portabilité COMPLÈTE : OAuth Gmail/Outlook + CVs + ConsentRecords +
    # Notifications. Avant P65, ces surfaces PII manquaient de l'export (refus de portabilité).
    oauth_integrations = db.query(models.UserEmailIntegration).filter_by(user_id=user_id).all()
    cvs = []
    if career:
        cvs = db.query(models.CV).filter_by(career_profile_id=career.id).all()
    consents = db.query(models.ConsentRecord).filter_by(tenant_id=tid, user_id=user_id).all()
    notifications = db.query(models.Notification).filter_by(tenant_id=tid, user_id=user_id).all()

    result = {
        "tenant_id": tid,
        "user_id": user_id,
        "exported_at": datetime.utcnow().isoformat(),
        "exported_by": {"user_id": _perm.user_id, "name": _perm.name},
        "user": {
            "id": user.id, "email": user.email, "name": user.name,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "locale": user.locale, "sso_id": user.sso_id,
            "identity_provider": user.identity_provider,
            "gdpr_erased_at": user.gdpr_erased_at.isoformat() if user.gdpr_erased_at else None,
        },
        "member": {
            "id": member.id, "email": member.email, "name": member.name,
            "role": member.role, "status": member.status,
            "added_at": member.added_at.isoformat() if member.added_at else None,
        },
        "contacts": [{"id": c.id, "first_name": c.first_name, "last_name": c.last_name,
                      "email": c.email, "phone": c.phone, "title": c.title,
                      "linkedin_url": c.linkedin_url, "company_id": c.company_id,
                      "score": c.score} for c in contacts],
        "audit_entries": [{"id": a.id, "created_at": a.created_at.isoformat() if a.created_at else None,
                           "action": a.action, "actor_name": a.actor_name,
                           "target_type": a.target_type, "target_id": a.target_id,
                           "detail": a.detail} for a in audit_entries],
        "career_profile": ({"id": career.id, "target_job_title": career.target_job_title,
                            "preferred_location": career.preferred_location,
                            "mobility": career.mobility} if career else None),
        "job_applications": [{"id": a.id, "posting_id": a.posting_id,
                              "adapted_cv_id": a.adapted_cv_id,
                              "created_at": a.created_at.isoformat() if a.created_at else None}
                             for a in job_apps],
        # P65 (P0-G2) — surfaces PII ajoutées pour portabilité complète (Art. 20).
        "email_integrations": [{"id": uei.id, "provider": uei.provider, "email": uei.email,
                                "scopes": uei.scopes,
                                "access_token_expires_at": uei.access_token_expires_at.isoformat() if uei.access_token_expires_at else None}
                               for uei in oauth_integrations],  # tokens chiffrés NON exportés (sensibles)
        "cvs": [{"id": cv.id, "title": cv.title, "raw_text": cv.raw_text,
                 "parsed_data": cv.parsed_data,
                 "source_filename": cv.source_filename,
                 "created_at": cv.created_at.isoformat() if cv.created_at else None}
                for cv in cvs],
        "consents": [{"id": c.id, "purpose": c.purpose,
                      "granted_at": c.granted_at.isoformat() if c.granted_at else None,
                      "withdrawn_at": c.withdrawn_at.isoformat() if c.withdrawn_at else None,
                      "privacy_policy_version": c.privacy_policy_version}
                     for c in consents],
        "notifications": [{"id": n.id, "type": n.type, "channel": n.channel,
                           "recipient_email": n.recipient_email, "subject": n.subject,
                           "body_text": n.body_text, "status": n.status,
                           "created_at": n.created_at.isoformat() if n.created_at else None}
                          for n in notifications],
        "counts": {"contacts": len(contacts), "audit_entries": len(audit_entries),
                   "job_applications": len(job_apps),
                   "email_integrations": len(oauth_integrations), "cvs": len(cvs),
                   "consents": len(consents), "notifications": len(notifications)},
    }
    # P69 (P1-7) — audit log de l'export (preuve DPO Art. 15/20 — qui a exporté les données de qui, quand).
    log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
              action="gdpr.export", target_type="user", target_id=user_id,
              detail={"exported_user_id": user_id, "counts": result["counts"]})
    db.commit()
    return result


@router.delete("/erase")
def gdpr_erase(
    tid: str,
    user_id: str = Query(..., description="ID du membre à anonymiser (GDPR Art. 17 right-to-be-forgotten)"),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """DELETE /gdpr/erase : anonymise le membre (right-to-be-forgotten GDPR Art. 17).
    Cascade : User (name/email scrubbed + gdpr_erased_at=now), Member (name/email/status scrubbed),
    Contacts détenus (first_name/last_name/email/phone/linkedin_url), AuditLog.actor_name scrubbed.
    Conservation structurelle (FK integrity + rétention audit SOC2 7 ans — GDPR Art. 17.3.b/e).
    Protection : impossible d'effacer soi-même ou le Owner du tenant (anti-lockout). RBAC workspace.manage."""
    # Protection anti-lockout : pas d'auto-effacement, pas d'effacement du Owner du tenant
    if user_id == _perm.user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Impossible d'effacer votre propre compte (anti-lockout) — demandez à un autre Owner")
    member = db.query(models.Member).filter_by(tenant_id=tid, user_id=user_id).first()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Aucun membre avec cet user_id dans ce tenant")
    if member.role == "Owner":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Impossible d'effacer le Owner du tenant (anti-lockout) — transférez d'abord l'ownership")
    user = db.query(models.User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User introuvable")
    if user.gdpr_erased_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"User déjà anonymisé (gdpr_erased_at={user.gdpr_erased_at.isoformat()})")

    now = datetime.utcnow()
    anonymized_tag = "Anonymized (GDPR)"
    # P65 (P0-G3) — capturer les PII originales AVANT scrub pour cascade (AuditLog.detail contient
    # l'email en clair dans des entries d'autres acteurs — notifications.py/crm.py loggent "email": email).
    original_email = user.email
    original_name = user.name

    # 1. User : scrub name + email (unique préservé via _erased_email) + gdpr_erased_at = now
    user.name = anonymized_tag
    user.email = _erased_email(user.id)
    user.gdpr_erased_at = now

    # 2. Member : scrub name + email + status
    member.name = anonymized_tag
    member.email = _erased_email(member.id)
    member.status = "gdpr_erased"

    # 3. Contacts détenus (owner_id) : scrub PII (first_name/last_name/email/phone/linkedin_url)
    contacts = db.query(models.Contact).filter_by(owner_id=user_id).all()
    for c in contacts:
        c.first_name = ""
        c.last_name = ""
        c.email = _erased_email(c.id)
        c.phone = ""
        c.linkedin_url = ""

    # 4. AuditLog.actor_name scrubbed (actor_user_id conservé pour traçabilité — immuabilité SOC2)
    audit_entries = db.query(models.AuditLog).filter_by(tenant_id=tid, actor_user_id=user_id).all()
    for a in audit_entries:
        a.actor_name = anonymized_tag

    # P65 (P0-G3) — Cascade effacement : surfaces PII qui survivaient à l'erase avant P65.

    # 4b. AuditLog.detail scrub : les entries (de TOUS les acteurs du tenant) dont le detail JSON contient
    # l'email original du user en clair (ex: notifications.py detail={"email": email}, crm.py). Sans ce scrub,
    # le user "anonymisé" reste traçable par son email dans l'audit trail → Art. 17 violé.
    def _scrub_detail(detail):
        """Remplace récursivement les occurrences de l'email/nom original dans un objet JSON."""
        if isinstance(detail, str):
            return detail.replace(original_email, _erased_email(user_id)).replace(original_name, anonymized_tag)
        if isinstance(detail, list):
            return [_scrub_detail(x) for x in detail]
        if isinstance(detail, dict):
            return {k: _scrub_detail(v) for k, v in detail.items()}
        return detail

    tenant_audit = db.query(models.AuditLog).filter_by(tenant_id=tid).all()
    detail_scrubbed = 0
    for a in tenant_audit:
        if a.detail and (original_email in str(a.detail) or original_name in str(a.detail)):
            a.detail = _scrub_detail(a.detail)
            detail_scrubbed += 1

    # 4c. UserEmailIntegration (OAuth Gmail/Outlook) : HARD DELETE — révoque l'accès (refresh_token +
    # access_token supprimés). Avant P65, les tokens restaient valides en DB + côté Google/Microsoft →
    # traitement continué post-effacement (Art. 17 + Art. 5(1)(b) violés).
    oauth_integrations = db.query(models.UserEmailIntegration).filter_by(user_id=user_id).all()
    for uei in oauth_integrations:
        db.delete(uei)

    # 4d. CV (CareerProfile → CV) : scrub raw_text + parsed_data (PII dense du candidat : experiences,
    # education, skills). Avant P65, CV.raw_text survivait → Art. 17 violé.
    cvs_scrubbed = 0
    if career_profile := db.query(models.CareerProfile).filter_by(user_id=user_id).first():
        cvs = db.query(models.CV).filter_by(career_profile_id=career_profile.id).all()
        for cv in cvs:
            cv.raw_text = ""
            cv.parsed_data = None
            cvs_scrubbed += 1

    db.commit()

    # 5. Log l'action GDPR elle-même (preuve de conformité — qui a exécuté le droit, quand, sur qui)
    log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
              action="gdpr.erase", target_type="user", target_id=user_id,
              detail={"anonymized_user_id": user_id, "contacts_anonymized": len(contacts),
                      "audit_entries_scrubbed": len(audit_entries),
                      "audit_detail_scrubbed": detail_scrubbed,
                      "oauth_integrations_deleted": len(oauth_integrations),
                      "cvs_scrubbed": cvs_scrubbed,
                      "member_id": member.id})
    db.commit()

    return {
        "user_id": user_id,
        "erased_at": now.isoformat(),
        "executed_by": {"user_id": _perm.user_id, "name": _perm.name},
        "member_id": member.id,
        "contacts_anonymized": len(contacts),
        "audit_entries_scrubbed": len(audit_entries),
        "audit_detail_scrubbed": detail_scrubbed,
        "oauth_integrations_deleted": len(oauth_integrations),
        "cvs_scrubbed": cvs_scrubbed,
        "note": "User/Member/Contacts/AuditLog.detail/OAuth-tokens/CVs anonymisés ou supprimés. "
                "Enregistrements structurels conservés (FK integrity + rétention SOC2 7 ans — GDPR Art. 17.3.b/e).",
    }


# ─── GDPR Art. 7 — Consent management (self-service, tout membre actif gère SON propre consent) ───

@router.get("/consent")
def gdpr_consent_list(
    tid: str,
    db: Session = Depends(get_db),
    membership: models.Member = Depends(get_membership),
):
    """GET /gdpr/consent : liste les consentements du membre courant (actifs + historique retiré).
    Self-service (tout membre actif, y compris Viewer) — chacun gère SON consent. Art. 7 proof."""
    records = db.query(models.ConsentRecord).filter_by(
        tenant_id=tid, user_id=membership.user_id
    ).order_by(models.ConsentRecord.granted_at.desc()).all()
    return [{
        "id": r.id, "purpose": r.purpose,
        "granted_at": r.granted_at.isoformat() if r.granted_at else None,
        "withdrawn_at": r.withdrawn_at.isoformat() if r.withdrawn_at else None,
        "active": r.withdrawn_at is None,
        "privacy_policy_version": r.privacy_policy_version,
        "ip": r.ip, "user_agent": r.user_agent,
    } for r in records]


@router.post("/consent", status_code=status.HTTP_201_CREATED)
def gdpr_consent_grant(
    tid: str,
    body: ConsentGrant,
    db: Session = Depends(get_db),
    membership: models.Member = Depends(get_membership),
):
    """POST /gdpr/consent : grant consent pour un purpose (Art. 7 — consentement explicite par finalité).
    Idempotent : si un consent actif existe déjà pour ce purpose, le retourne sans recréer.
    Capture IP + UA au moment du grant (preuve Art. 7.1). Self-service (tout membre actif)."""
    if body.purpose not in _CONSENT_PURPOSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"purpose must be one of: {sorted(_CONSENT_PURPOSES)}")
    # Idempotent : consent actif existe déjà ?
    existing = db.query(models.ConsentRecord).filter_by(
        tenant_id=tid, user_id=membership.user_id, purpose=body.purpose,
        withdrawn_at=None).first()
    if existing:
        return {"id": existing.id, "purpose": existing.purpose,
                "granted_at": existing.granted_at.isoformat(),
                "privacy_policy_version": existing.privacy_policy_version,
                "active": True, "note": "Consent déjà actif (idempotent)"}
    rec = models.ConsentRecord(
        tenant_id=tid, user_id=membership.user_id, purpose=body.purpose,
        granted_at=datetime.utcnow(), privacy_policy_version=body.privacy_policy_version,
        ip=request_ip_var.get(""), user_agent=request_ua_var.get(""))
    db.add(rec)
    db.commit()
    # Log audit (preuve DPO : qui a consenti, pour quel purpose, quelle version PP)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="gdpr.consent.grant", target_type="consent", target_id=rec.id,
              detail={"purpose": body.purpose, "privacy_policy_version": body.privacy_policy_version})
    db.commit()
    return {"id": rec.id, "purpose": rec.purpose, "granted_at": rec.granted_at.isoformat(),
            "privacy_policy_version": rec.privacy_policy_version, "active": True}


@router.delete("/consent")
def gdpr_consent_withdraw(
    tid: str,
    purpose: str = Query(..., description="Purpose du consent à retirer (Art. 7.3 right to withdraw)"),
    db: Session = Depends(get_db),
    membership: models.Member = Depends(get_membership),
):
    """DELETE /gdpr/consent?purpose=X : retire le consent actif pour ce purpose (Art. 7.3).
    Set withdrawn_at = now (historique immuable conservé — preuve DPO du retrait). Self-service."""
    if purpose not in _CONSENT_PURPOSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"purpose must be one of: {sorted(_CONSENT_PURPOSES)}")
    rec = db.query(models.ConsentRecord).filter_by(
        tenant_id=tid, user_id=membership.user_id, purpose=purpose,
        withdrawn_at=None).first()
    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Aucun consent actif pour purpose='{purpose}'")
    rec.withdrawn_at = datetime.utcnow()
    db.flush()
    # P65 (P0-G1) — GDPR Art. 7.3 : le retrait consent marketing ARRÊTE le traitement associé.
    # Pause des SequenceEnrollment actifs créés par ce user dans CE tenant (drip campaigns en cours).
    # Avant P65, withdrawn_at était posé mais les séquences continuaient → fausse conformité CNIL.
    paused_sequences = 0
    if purpose == "marketing":
        active_enrollments = (
            db.query(models.SequenceEnrollment)
            .join(models.EmailSequence, models.SequenceEnrollment.sequence_id == models.EmailSequence.id)
            .filter(models.SequenceEnrollment.enrolled_by_user_id == membership.user_id,
                    models.EmailSequence.tenant_id == tid,
                    models.SequenceEnrollment.status == "active")
            .all()
        )
        for enr in active_enrollments:
            enr.status = "paused"
        paused_sequences = len(active_enrollments)
    db.commit()
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="gdpr.consent.withdraw", target_type="consent", target_id=rec.id,
              detail={"purpose": purpose, "sequences_paused": paused_sequences})
    db.commit()
    return {"id": rec.id, "purpose": rec.purpose,
            "withdrawn_at": rec.withdrawn_at.isoformat(), "active": False,
            "sequences_paused": paused_sequences,
            "note": "Consent retiré (Art. 7.3) — traitement ARRÊTÉ (séquences pausées) + historique conservé (preuve DPO)"}
