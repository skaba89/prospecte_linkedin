"""P34 — Career router : référentiel métiers + CV (espace candidat isolé du Business).

Endpoints (préfixe /api/career — scoped par user_id via get_current_user, PAS de tenant_id) :
  GET    /profile              — profil Career du candidat courant (404 si aucun).
  PUT    /profile              — upsert profil Career (lazy create si premier).
  POST   /cv/text              — importer un CV depuis texte brut.
  POST   /cv/pdf               — importer un CV depuis PDF (UploadFile).
  POST   /cv/docx              — importer un CV depuis Word .docx (UploadFile).
  GET    /cv                   — lister les CV du candidat (plus récents d'abord).
  GET    /cv/{id}              — détail d'un CV (+ parsed_data).
  POST   /cv/{id}/activate     — activer un CV (désactive les autres — 1 seul actif).
  GET    /cv/{id}/transferrable — compétences transférables détectées (soft skills cross-domain).
  GET    /referential/jobs     — rechercher dans le référentiel métiers (ROME/ESCO/O*NET).

Isolation Career (P34) :
  - Tous les endpoints sont scoped par user_id (get_current_user). PAS de tenant_id — un candidat
    peut être Member d'un tenant Business (côté prospecteur) ET avoir un CareerProfile (côté candidat)
    sans que les données Career soient accessibles via le tenant Business.
  - Chaque CV est vérifié comme appartenant au profile du candidat courant (profile.user_id == user.id).
  - Le référentiel métiers est global (lecture publique pour candidats authentifiés).

Sécurité (P34) :
  - raw_text stocké (jamais le binaire PDF/Word) — anti-fuite du fichier original.
  - Extraction heuristique v1 (parsed_data.heuristic=True) — pas présentée comme ML/NLP parfait.
  - n'invente JAMAIS de compétence (P37 invariant) — parse_cv n'ajoute que ce qui est dans le texte.
"""
import io
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..database import get_db
from ..deps import get_current_user
from ..services import (
    career_analytics,
    career_cv,
    career_notifier,
    career_referential,
    cover_letter,
    cv_adapter,
    cv_generator,
    email_sender,
    interview_coach,
    job_search,
    matching,
)
from ..services.job_connectors import SearchCriteria, list_connectors

router = APIRouter(prefix="/api/career", tags=["career"])


def _get_or_404_profile(db: Session, user_id: str) -> models.CareerProfile:
    """Récupère le CareerProfile du user ou 404 (le candidat doit PUT /profile d'abord)."""
    profile = db.query(models.CareerProfile).filter_by(user_id=user_id).first()
    if not profile:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Aucun profil Career — créez-le via PUT /api/career/profile.")
    return profile


def _own_cv(db: Session, cv_id: str, user_id: str) -> models.CV:
    """Récupère un CV + vérifie qu'il appartient au profile du user courant (isolation stricte)."""
    cv = db.query(models.CV).filter_by(id=cv_id).first()
    if not cv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CV introuvable.")
    profile = db.query(models.CareerProfile).filter_by(id=cv.career_profile_id).first()
    if not profile or profile.user_id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CV inaccessible — isolation Career.")
    return cv


# ─── Profil Career ───────────────────────────────────────────────────────────


@router.get("/profile", response_model=schemas.CareerProfileOut)
def get_profile(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Profil Career du candidat courant (404 si aucun — créer via PUT /profile)."""
    profile = db.query(models.CareerProfile).filter_by(user_id=user.id).first()
    if not profile:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aucun profil Career — créez-le via PUT /api/career/profile.")
    return profile


@router.put("/profile", response_model=schemas.CareerProfileOut)
def upsert_profile(
    body: schemas.CareerProfileRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Upsert profil Career (lazy create si premier). Isolation : scoped par user_id unique."""
    profile = db.query(models.CareerProfile).filter_by(user_id=user.id).first()
    if profile:
        profile.target_job_title = body.target_job_title
        profile.preferred_location = body.preferred_location
        profile.mobility = body.mobility
        profile.contract_types = body.contract_types
        profile.salary_min = body.salary_min
        profile.salary_max = body.salary_max
        profile.tjm_min = body.tjm_min
        profile.currency = body.currency
        profile.languages = body.languages
        # P40 — prefs notification (optionnelles, opt-in explicite)
        if body.alert_enabled is not None:
            profile.alert_enabled = body.alert_enabled
        if body.alert_min_score is not None:
            profile.alert_min_score = body.alert_min_score
        if body.alert_channels is not None:
            profile.alert_channels = body.alert_channels
        if body.alert_frequency is not None:
            profile.alert_frequency = body.alert_frequency
        action = "career.profile.update"
    else:
        profile = models.CareerProfile(
            user_id=user.id,
            target_job_title=body.target_job_title,
            preferred_location=body.preferred_location,
            mobility=body.mobility,
            contract_types=body.contract_types,
            salary_min=body.salary_min,
            salary_max=body.salary_max,
            tjm_min=body.tjm_min,
            currency=body.currency,
            languages=body.languages,
            alert_enabled=body.alert_enabled if body.alert_enabled is not None else True,
            alert_min_score=body.alert_min_score if body.alert_min_score is not None else 70,
            alert_channels=body.alert_channels if body.alert_channels is not None else ["in_app"],
            alert_frequency=body.alert_frequency if body.alert_frequency is not None else "daily",
        )
        db.add(profile)
        action = "career.profile.create"
    db.commit()
    db.refresh(profile)
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action=action, target_type="career_profile", target_id=profile.id,
              detail={"target_job_title": body.target_job_title})
    db.commit()
    db.refresh(profile)
    return profile


# ─── CV : import (text/PDF/Word) + CRUD ──────────────────────────────────────


@router.post("/cv/text", response_model=schemas.CVOut, status_code=status.HTTP_201_CREATED)
def import_cv_text(
    body: schemas.CVTextImport,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Importe un CV depuis texte brut. Parse heuristique (skills/experiences/certifications/education)."""
    profile = _get_or_404_profile(db, user.id)
    try:
        cv = career_cv.import_cv_text(db, profile, body.title, body.text, body.locale)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action="career.cv.import_text", target_type="cv", target_id=cv.id,
              detail={"title": body.title, "version": cv.version})
    db.commit()
    db.refresh(cv)
    return cv


@router.post("/cv/pdf", response_model=schemas.CVOut, status_code=status.HTTP_201_CREATED)
def import_cv_pdf(
    title: str,
    file: UploadFile = File(...),
    locale: str = "fr",
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Importe un CV depuis un PDF. Extrait le texte via pdfminer (layout-aware). raw_text stocké (pas le binaire)."""
    profile = _get_or_404_profile(db, user.id)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Le fichier doit être un .pdf")
    file_bytes = file.file.read()
    try:
        cv = career_cv.import_cv_pdf(db, profile, title, file.filename, file_bytes, locale)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action="career.cv.import_pdf", target_type="cv", target_id=cv.id,
              detail={"title": title, "filename": file.filename})
    db.commit()
    db.refresh(cv)
    return cv


@router.post("/cv/docx", response_model=schemas.CVOut, status_code=status.HTTP_201_CREATED)
def import_cv_docx(
    title: str,
    file: UploadFile = File(...),
    locale: str = "fr",
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Importe un CV depuis un Word .docx. Extrait le texte via python-docx. raw_text stocké (pas le binaire)."""
    profile = _get_or_404_profile(db, user.id)
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Le fichier doit être un .docx")
    file_bytes = file.file.read()
    try:
        cv = career_cv.import_cv_docx(db, profile, title, file.filename, file_bytes, locale)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action="career.cv.import_docx", target_type="cv", target_id=cv.id,
              detail={"title": title, "filename": file.filename})
    db.commit()
    db.refresh(cv)
    return cv


@router.get("/cv", response_model=list[schemas.CVOut])
def list_cvs(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Liste les CV du candidat (plus récents d'abord)."""
    profile = _get_or_404_profile(db, user.id)
    return db.query(models.CV).filter_by(career_profile_id=profile.id).order_by(
        models.CV.created_at.desc()).all()


@router.get("/cv/{cv_id}", response_model=schemas.CVOut)
def get_cv(
    cv_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Détail d'un CV (+ parsed_data). Vérifie l'appartenance au candidat courant."""
    return _own_cv(db, cv_id, user.id)


@router.post("/cv/{cv_id}/activate", response_model=schemas.CVOut)
def activate_cv(
    cv_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Active un CV (désactive les autres du même profile — 1 seul actif)."""
    cv = _own_cv(db, cv_id, user.id)
    career_cv.set_active_cv(db, cv)
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action="career.cv.activate", target_type="cv", target_id=cv.id,
              detail={"version": cv.version})
    db.commit()
    db.refresh(cv)
    return cv


@router.get("/cv/{cv_id}/transferrable", response_model=schemas.TransferrableSkillsOut)
def transferrable_skills(
    cv_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Compétences transférables détectées (soft skills cross-domain — utile pour un changement de métier)."""
    cv = _own_cv(db, cv_id, user.id)
    skills = career_cv.detect_transferrable_skills(cv.parsed_data or {})
    return {"cv_id": cv.id, "transferrable_skills": skills}


# ─── Référentiel métiers (global, lecture publique candidats authentifiés) ───


@router.get("/referential/jobs", response_model=list[schemas.JobReferenceOut])
def search_jobs(
    q: str | None = None,
    source: str | None = None,
    category: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Recherche dans le référentiel métiers (ROME/ESCO/O*NET). Filtre optionnel par query, source, category."""
    return career_referential.search_jobs(db, query=q, source=source, category=category, limit=limit)


# ─── P35 : recherche d'opportunités (connecteurs + postings + saved searches) ─


@router.get("/jobs/connectors", response_model=list[schemas.ConnectorInfoOut])
def list_job_connectors(
    user: models.User = Depends(get_current_user),
):
    """Liste les 12 connecteurs job boards avec leur statut de configuration (transparence : un connecteur
    non configuré est visible — JAMAIS masqué). Pour UI + documentation."""
    return list_connectors()


@router.post("/jobs/search", response_model=schemas.SearchResponse)
def search_job_postings(
    body: schemas.SearchCriteriaRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Recherche multicritère across les connecteurs configurés. Persiste les postings (dedup hash).
    Un connecteur non configuré → status "not_configured" (visible, 0 postings — JAMAIS de faux succès)."""
    criteria = SearchCriteria(
        keywords=body.keywords, location=body.location, contract_types=body.contract_types,
        salary_min=body.salary_min, salary_max=body.salary_max, radius_km=body.radius_km,
        source_connectors=body.source_connectors,
    )
    return job_search.search_jobs(db, criteria)


@router.get("/jobs/postings", response_model=list[schemas.JobPostingOut])
def list_job_postings(
    keywords: str | None = None,
    location: str | None = None,
    source: str | None = None,
    include_expired: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Liste les offres cacheées (postings persistés). Filtre keywords/location/source. include_expired=False
    par défaut (offres actives seulement). Cap 50 anti-abus."""
    q = db.query(models.JobPosting)
    if not include_expired:
        q = q.filter_by(is_expired=False)
    if source:
        q = q.filter_by(source_connector=source)
    if keywords:
        kw = keywords.lower()
        q = q.filter(models.JobPosting.title.ilike(f"%{kw}%"))
    if location:
        loc = location.lower()
        q = q.filter(models.JobPosting.location.ilike(f"%{loc}%"))
    return q.order_by(models.JobPosting.fetched_at.desc()).limit(limit).all()


# ─── Recherches sauvegardées ─────────────────────────────────────────────────


@router.get("/saved-searches", response_model=list[schemas.SavedSearchOut])
def list_saved_searches(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Liste les recherches sauvegardées du candidat."""
    profile = _get_or_404_profile(db, user.id)
    return db.query(models.SavedSearch).filter_by(career_profile_id=profile.id).order_by(
        models.SavedSearch.created_at.desc()).all()


@router.post("/saved-searches", response_model=schemas.SavedSearchOut, status_code=status.HTTP_201_CREATED)
def create_saved_search(
    body: schemas.SavedSearchRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Crée une recherche sauvegardée (criteria + alerte optionnelle)."""
    profile = _get_or_404_profile(db, user.id)
    saved = models.SavedSearch(
        career_profile_id=profile.id,
        name=body.name,
        criteria=body.criteria.model_dump(),
        alert_enabled=body.alert_enabled,
        alert_frequency=body.alert_frequency,
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action="career.saved_search.create", target_type="saved_search", target_id=saved.id,
              detail={"name": body.name})
    db.commit()
    db.refresh(saved)
    return saved


@router.post("/saved-searches/{search_id}/run", response_model=schemas.SearchResponse)
def run_saved_search(
    search_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Exécute une recherche sauvegardée + met à jour last_run_at + count."""
    profile = _get_or_404_profile(db, user.id)
    saved = db.query(models.SavedSearch).filter_by(id=search_id, career_profile_id=profile.id).first()
    if not saved:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recherche sauvegardée introuvable.")
    return job_search.run_saved_search(db, saved)


@router.delete("/saved-searches/{search_id}")
def delete_saved_search(
    search_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Supprime une recherche sauvegardée (isolation : profile.user_id vérifié). Retourne {"deleted": id}
    (cohérent avec les autres DELETE RevPulse — CRM contacts/sequences/webhooks)."""
    profile = _get_or_404_profile(db, user.id)
    saved = db.query(models.SavedSearch).filter_by(id=search_id, career_profile_id=profile.id).first()
    if not saved:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recherche sauvegardée introuvable.")
    db.delete(saved)
    db.commit()
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action="career.saved_search.delete", target_type="saved_search", target_id=search_id)
    db.commit()
    return {"deleted": search_id}


# ─── P36 : matching intelligent CV↔offre (score hybride explicable 0-100) ────


@router.post("/matching/{cv_id}/{posting_id}", response_model=schemas.MatchingBreakdownOut)
def compute_match(
    cv_id: str,
    posting_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Calcule le score de matching CV↔offre (P36). 6 critères pondérés (skills 40%, experience 20%,
    certifications/location/salary/languages 10% chacun). Score total 0-100 (capé à 60 si éliminatoire).
    Isolation : CV doit appartenir au profil du user + posting doit exister (non expiré privilégié).
    Disclaimer : score = compatibilité, NE PAS présenter comme probabilité de recrutement."""
    profile = _get_or_404_profile(db, user.id)
    cv = db.query(models.CV).filter_by(id=cv_id, career_profile_id=profile.id).first()
    if not cv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CV introuvable (isolation profil).")
    posting = db.query(models.JobPosting).filter_by(id=posting_id).first()
    if not posting:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Offre introuvable.")
    return matching.compute_match(cv, posting, profile)


@router.post("/matching/search", response_model=list[schemas.MatchingRankedItemOut])
def matching_search(
    body: schemas.MatchingSearchRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Match le CV (spécifié ou actif) contre N postings actifs → liste ranked par total_score décroissant.
    Cap limit à 50 anti-abus. Disclaimer : scores = compatibilité (pas probabilité de recrutement)."""
    profile = _get_or_404_profile(db, user.id)
    if body.cv_id:
        cv = db.query(models.CV).filter_by(id=body.cv_id, career_profile_id=profile.id).first()
    else:
        cv = db.query(models.CV).filter_by(career_profile_id=profile.id, is_active=True).first()
    if not cv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CV introuvable (spécifiez cv_id ou activez un CV).")
    q = db.query(models.JobPosting).filter_by(is_expired=False)
    if body.source:
        q = q.filter_by(source_connector=body.source)
    if body.keywords:
        kw = body.keywords.lower()
        q = q.filter(models.JobPosting.title.ilike(f"%{kw}%"))
    if body.location:
        loc = body.location.lower()
        q = q.filter(models.JobPosting.location.ilike(f"%{loc}%"))
    postings = q.order_by(models.JobPosting.fetched_at.desc()).limit(body.limit).all()
    results: list[schemas.MatchingRankedItemOut] = []
    for p in postings:
        bd = matching.compute_match(cv, p, profile)
        results.append(schemas.MatchingRankedItemOut(
            posting=schemas.JobPostingOut.model_validate(p),
            total_score=bd.total_score, missing_skills=bd.missing_skills,
            eliminatory_flags=bd.eliminatory_flags,
        ))
    results.sort(key=lambda r: r.total_score, reverse=True)
    # P40 — génère les notifications new_match pour les offres ≥ alert_min_score (opt-in + dedup anti-spam
    # gérés par career_notifier.notify_new_match : alert_enabled False → skip, déjà notifié → skip).
    # Une notif en échec ne doit jamais casser le matching (try/except pass).
    scored = {r.posting.id: r.total_score for r in results}
    for p in postings:
        if scored.get(p.id, 0) >= (profile.alert_min_score or 0):
            try:
                career_notifier.notify_new_match(db, profile, cv.id, p, scored[p.id])
            except Exception:
                pass
    return results


# ─── P37 : CV adaptatif (versions ciblées par offre, anti-invention) ──────────


@router.post("/cv/{cv_id}/adapt", response_model=schemas.AdaptedCVOut, status_code=status.HTTP_201_CREATED)
def adapt_cv(
    cv_id: str,
    body: schemas.AdaptCVRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Adapte le CV à une offre cible (P37). Réordonne skills par pertinence + sélectionne expériences
    alignées + summary ciblé. Invariant P37 : n'ajoute QUE ce qui est dans le CV source (anti-invention
    vérifié en runtime par cv_adapter._assert_no_invention). posting_id OU target_job_title requis."""
    profile = _get_or_404_profile(db, user.id)
    cv = db.query(models.CV).filter_by(id=cv_id, career_profile_id=profile.id).first()
    if not cv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CV introuvable (isolation profil).")
    posting = None
    target_title = body.target_job_title
    target_company = body.target_company
    if body.posting_id:
        posting = db.query(models.JobPosting).filter_by(id=body.posting_id).first()
        if not posting:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Offre cible introuvable.")
        target_title = target_title or posting.title
        target_company = target_company or posting.company
    elif not target_title:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "posting_id OU target_job_title requis.")
    adapted_data = cv_adapter.adapt_cv(cv, posting, profile)
    adapted_data["targeted_title"] = target_title or adapted_data.get("targeted_title")
    acv = models.AdaptedCV(
        cv_source_id=cv.id, career_profile_id=profile.id,
        posting_id=body.posting_id, target_job_title=target_title, target_company=target_company,
        adapted_data=adapted_data, match_score=adapted_data.get("match_score"),
    )
    db.add(acv)
    db.commit()
    db.refresh(acv)
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action="career.cv.adapt", target_type="adapted_cv", target_id=acv.id,
              detail={"cv_source_id": cv.id, "posting_id": body.posting_id, "match_score": acv.match_score})
    db.commit()
    db.refresh(acv)
    return acv


@router.get("/cv-adapted", response_model=list[schemas.AdaptedCVOut])
def list_adapted_cvs(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Liste les CV adaptés du candidat (historique des adaptations générées)."""
    profile = _get_or_404_profile(db, user.id)
    return db.query(models.AdaptedCV).filter_by(career_profile_id=profile.id).order_by(
        models.AdaptedCV.created_at.desc()).all()


@router.get("/cv-adapted/{adapted_id}", response_model=schemas.AdaptedCVOut)
def get_adapted_cv(
    adapted_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Récupère un CV adapté par id (isolation : career_profile_id vérifié)."""
    profile = _get_or_404_profile(db, user.id)
    acv = db.query(models.AdaptedCV).filter_by(id=adapted_id, career_profile_id=profile.id).first()
    if not acv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CV adapté introuvable.")
    return acv


@router.get("/cv-adapted/{adapted_id}/download")
def download_adapted_cv(
    adapted_id: str,
    format: str = "pdf",  # pdf|docx
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Télécharge le CV adapté en PDF ou Word (.docx). format=pdf (default) ou docx.
    Le fichier est généré à la volée (cv_generator) à partir de adapted_data — pas de stockage fichier."""
    if format not in ("pdf", "docx"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "format doit être 'pdf' ou 'docx'.")
    profile = _get_or_404_profile(db, user.id)
    acv = db.query(models.AdaptedCV).filter_by(id=adapted_id, career_profile_id=profile.id).first()
    if not acv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CV adapté introuvable.")
    cv = db.query(models.CV).filter_by(id=acv.cv_source_id).first()
    if format == "pdf":
        content = cv_generator.generate_pdf(acv.adapted_data, profile, cv)
        media = "application/pdf"
        ext = "pdf"
    else:
        content = cv_generator.generate_docx(acv.adapted_data, profile, cv)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ext = "docx"
    filename = f"cv_adapte_{acv.id}.{ext}"
    return StreamingResponse(io.BytesIO(content), media_type=media,
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ─── P38 : Candidatures + Gmail/Outlook (pipeline de candidatures) ────────────


_APPLICATION_STATUSES = ["submitted", "interview", "offer", "rejected", "withdrawn"]


@router.post("/applications", response_model=schemas.JobApplicationOut, status_code=status.HTTP_201_CREATED)
def create_application(
    body: schemas.ApplicationCreateRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Crée une candidature (P38). company + job_title requis. posting_id/adapted_cv_id optionnels.
    generate_cover_letter=True → génère la lettre depuis le CV source (anti-invention P37/P38 invariant).
    Anti-spam : envoi email séparé (POST /applications/{id}/send) — consentement explicite."""
    profile = _get_or_404_profile(db, user.id)
    # Validate posting_id si fourni.
    posting = None
    if body.posting_id:
        posting = db.query(models.JobPosting).filter_by(id=body.posting_id).first()
        if not posting:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Offre cible introuvable.")
    # Validate adapted_cv_id si fourni (isolation profil).
    adapted_cv = None
    if body.adapted_cv_id:
        adapted_cv = db.query(models.AdaptedCV).filter_by(id=body.adapted_cv_id, career_profile_id=profile.id).first()
        if not adapted_cv:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "CV adapté introuvable (isolation profil).")
    # Cover letter auto-générée (anti-invention) si demandé.
    cover = None
    if body.generate_cover_letter:
        if not body.cv_source_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "cv_source_id requis pour generate_cover_letter.")
        cv_src = db.query(models.CV).filter_by(id=body.cv_source_id, career_profile_id=profile.id).first()
        if not cv_src:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "CV source introuvable (isolation profil).")
        cover = cover_letter.generate_cover_letter(cv_src, posting, profile, candidate_name=user.name)
    company = body.company or (posting.company if posting else None)
    job_title = body.job_title or (posting.title if posting else None)
    app = models.JobApplication(
        career_profile_id=profile.id, posting_id=body.posting_id, adapted_cv_id=body.adapted_cv_id,
        company=company, job_title=job_title, status="submitted", cover_letter=cover,
        application_method=body.application_method, recipient_email=body.recipient_email,
        notes=body.notes, timeline=[{"step": "created", "status": "submitted",
                                     "date": datetime.utcnow().isoformat(), "note": "Candidature créée."}],
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action="career.application.create", target_type="job_application", target_id=app.id,
              detail={"company": company, "job_title": job_title, "method": body.application_method})
    db.commit()
    db.refresh(app)
    return app


@router.get("/applications", response_model=list[schemas.JobApplicationOut])
def list_applications(
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Liste les candidatures du candidat (P38). Filtre optionnel par status (pipeline)."""
    profile = _get_or_404_profile(db, user.id)
    q = db.query(models.JobApplication).filter_by(career_profile_id=profile.id)
    if status_filter:
        if status_filter not in _APPLICATION_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status doit être dans {_APPLICATION_STATUSES}.")
        q = q.filter_by(status=status_filter)
    return q.order_by(models.JobApplication.applied_at.desc()).all()


@router.get("/applications/{application_id}", response_model=schemas.JobApplicationOut)
def get_application(
    application_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Récupère une candidature par id (isolation : career_profile_id vérifié)."""
    profile = _get_or_404_profile(db, user.id)
    app = db.query(models.JobApplication).filter_by(id=application_id, career_profile_id=profile.id).first()
    if not app:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidature introuvable.")
    return app


@router.patch("/applications/{application_id}", response_model=schemas.JobApplicationOut)
def update_application(
    application_id: str,
    body: schemas.ApplicationUpdateRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Met à jour une candidature (P38) — tracking pipeline. status (submitted/interview/offer/rejected/
    withdrawn). Chaque changement de status ajoute une entrée au timeline (historique audit trail)."""
    profile = _get_or_404_profile(db, user.id)
    app = db.query(models.JobApplication).filter_by(id=application_id, career_profile_id=profile.id).first()
    if not app:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidature introuvable.")
    if body.status:
        if body.status not in _APPLICATION_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status doit être dans {_APPLICATION_STATUSES}.")
        if body.status != app.status:
            old_status = app.status  # P40 — capturé avant mutation pour la notif application_status
            timeline = list(app.timeline or [])  # nouvel objet — SQLAlchemy ne détecte pas les mutations in-place JSON.
            timeline.append({"step": "status_change", "status": body.status,
                             "date": datetime.utcnow().isoformat(),
                             "note": f"Statut changé : {app.status} → {body.status}."})
            app.timeline = timeline
            app.status = body.status
            _pending_status_change = (old_status, body.status)  # notif P40 après commit
        else:
            _pending_status_change = None
    else:
        _pending_status_change = None
    if body.notes is not None:
        app.notes = body.notes
    db.commit()
    db.refresh(app)
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action="career.application.update", target_type="job_application", target_id=app.id,
              detail={"status": app.status})
    db.commit()
    db.refresh(app)
    # P40 — notif application_status sur changement vers interview/offer/rejected (opt-in + dedup anti-spam
    # gérés par career_notifier : alert_enabled False → skip, déjà notifié pour (app_id, new_status) → skip).
    if _pending_status_change:
        try:
            career_notifier.notify_application_status(db, profile, app,
                                                      _pending_status_change[0], _pending_status_change[1])
        except Exception:
            pass  # une notif en échec ne doit pas casser la MAJ candidature
    return app


@router.post("/applications/{application_id}/send", response_model=schemas.JobApplicationOut)
def send_application(
    application_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Envoie une candidature via email (P38) — CONSENTEMENT EXPLICITE (POST explicite, pas d'auto).
    Anti-spam : une candidature à la fois, rate-limit 50/jour. v1 : stub not_configured (OAuth2 Gmail/Outlook
    non configuré — comme P35 connectors). v2 : OAuth2 concret."""
    profile = _get_or_404_profile(db, user.id)
    app = db.query(models.JobApplication).filter_by(id=application_id, career_profile_id=profile.id).first()
    if not app:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidature introuvable.")
    if app.application_method != "email":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "application_method doit être 'email' pour envoi.")
    result = email_sender.send_application_email(app, profile, db)
    if result.status == "error":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, result.error or "Erreur envoi email.")
    # not_configured → on enregistre la tentative mais sent_at reste None (v1 stub).
    if result.status == "sent":
        app.sent_at = result.sent_at or datetime.utcnow()
        timeline = list(app.timeline or [])  # nouvel objet — SQLAlchemy ne détecte pas les mutations in-place JSON.
        timeline.append({"step": "sent", "status": app.status,
                         "date": app.sent_at.isoformat(), "note": f"Envoyé via {result.provider}."})
        app.timeline = timeline
    db.commit()
    db.refresh(app)
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action="career.application.send", target_type="job_application", target_id=app.id,
              detail={"send_status": result.status, "provider": result.provider})
    db.commit()
    db.refresh(app)
    return app


# ─── P40 — Notifications intelligentes ────────────────────────────────────────
@router.get("/notifications", response_model=list[schemas.CareerNotificationOut])
def list_notifications(
    unread_only: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Liste les notifications Career (P40). unread_only=True → filtre non lus. Isolation user_id."""
    return career_notifier.list_notifications(db, user.id, unread_only=unread_only, limit=limit)


@router.get("/notifications/unread-count")
def notifications_unread_count(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Nombre de notifications non lues (badge dashboard). P40."""
    return {"unread": career_notifier.unread_count(db, user.id)}


@router.post("/notifications/{notif_id}/read", response_model=schemas.CareerNotificationOut)
def mark_notification_read(
    notif_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Marque une notification comme lue (idempotent). Isolation user_id (404 si notif d'un autre user)."""
    n = career_notifier.mark_read(db, user.id, notif_id)
    if not n:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification introuvable.")
    return n


@router.post("/notifications/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Marque toutes les notifications non lues comme lues. P40. Retourne le nombre marqué."""
    count = career_notifier.mark_all_read(db, user.id)
    return {"marked_read": count}


@router.get("/notifications/prefs", response_model=schemas.NotificationPrefsOut)
def get_notification_prefs(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Préférences notification Career (P40). alert_enabled = opt-in explicite (anti-spam)."""
    profile = _get_or_404_profile(db, user.id)
    return profile


@router.put("/notifications/prefs", response_model=schemas.NotificationPrefsOut)
def update_notification_prefs(
    body: schemas.NotificationPrefsUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Met à jour les préférences notification (P40). opt-in/opt-out explicite anti-spam.
    alert_enabled=False → aucune notification future (notify_* retourne None)."""
    profile = _get_or_404_profile(db, user.id)
    if body.alert_enabled is not None:
        profile.alert_enabled = body.alert_enabled
    if body.alert_min_score is not None:
        profile.alert_min_score = body.alert_min_score
    if body.alert_channels is not None:
        # validation : canaux autorisés uniquement
        valid = [c for c in body.alert_channels if c in ("in_app", "email")]
        profile.alert_channels = valid or ["in_app"]
    if body.alert_frequency is not None:
        profile.alert_frequency = body.alert_frequency
    db.commit()
    db.refresh(profile)
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action="career.notification_prefs.update", target_type="career_profile",
              target_id=profile.id,
              detail={"alert_enabled": profile.alert_enabled, "alert_min_score": profile.alert_min_score})
    db.commit()
    db.refresh(profile)
    return profile


# ─── P41 — Coach IA entretiens ────────────────────────────────────────────────
@router.post("/interviews", response_model=schemas.InterviewSessionOut, status_code=status.HTTP_201_CREATED)
def start_interview_session(
    body: schemas.InterviewStartRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Démarre une session de simulation d'entretien (P41). Génère des questions adaptées au métier
    (catégorie auto-détectée depuis target_job_title ou explicite). session_type : tech | rhmixed.
    Lien optionnel à une candidature (application_id). Anti-invention : questions issues de la banque."""
    profile = _get_or_404_profile(db, user.id)
    if body.application_id:
        app = db.query(models.JobApplication).filter_by(id=body.application_id, career_profile_id=profile.id).first()
        if not app:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidature introuvable.")
    questions = interview_coach.generate_questions(body.target_job_title, body.category, body.session_type)
    session = models.InterviewSession(
        career_profile_id=profile.id, application_id=body.application_id,
        target_job_title=body.target_job_title, category=interview_coach._category_for(body.target_job_title, body.category),
        session_type=body.session_type, status="in_progress", questions=questions, answers=[],
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action="career.interview.start", target_type="interview_session", target_id=session.id,
              detail={"target_job_title": body.target_job_title, "session_type": body.session_type})
    db.commit()
    db.refresh(session)
    return session


@router.post("/interviews/{session_id}/answer", response_model=schemas.InterviewSessionOut)
def submit_interview_answer(
    session_id: str,
    body: schemas.AnswerRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Soumet une réponse à une question (P41). Analyse heuristic (keywords) + feedback factuel.
    Anti-invention : aucune affirmation non vérifiable — feedback basé sur expected_keywords."""
    profile = _get_or_404_profile(db, user.id)
    session = db.query(models.InterviewSession).filter_by(id=session_id, career_profile_id=profile.id).first()
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session introuvable.")
    if session.status == "completed":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Session déjà terminée.")
    question = next((q for q in (session.questions or []) if q["id"] == body.question_id), None)
    if not question:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Question {body.question_id} introuvable dans la session.")
    # CV actif pour cross-référence (anti-invention : feedback basé sur le CV du candidat)
    cv = db.query(models.CV).filter_by(career_profile_id=profile.id, is_active=True).first()
    cv_data = cv.parsed_data if cv else None
    analysis = interview_coach.analyze_answer(question, body.answer_text, cv_data)
    answers = list(session.answers or [])
    # retire une éventuelle réponse précédente à la même question (re-passage)
    answers = [a for a in answers if a.get("question_id") != body.question_id]
    answers.append({**analysis, "answer_text": body.answer_text})
    session.answers = answers
    db.commit()
    db.refresh(session)
    return session


@router.post("/interviews/{session_id}/complete", response_model=schemas.InterviewSessionOut)
def complete_interview_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Termine la session (P41) : calcule le score global + feedback (missing skills + formations +
    negotiation prep). Anti-invention : missing skills cross-référence CV, negotiation basé sur profil."""
    profile = _get_or_404_profile(db, user.id)
    session = db.query(models.InterviewSession).filter_by(id=session_id, career_profile_id=profile.id).first()
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session introuvable.")
    if session.status == "completed":
        return session  # idempotent
    cv = db.query(models.CV).filter_by(career_profile_id=profile.id, is_active=True).first()
    cv_data = cv.parsed_data if cv else None
    session.feedback = interview_coach.build_session_feedback(db, session, cv_data)
    session.score = session.feedback["overall_score"]
    session.status = "completed"
    session.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(session)
    log_audit(db, tenant_id="career", actor_user_id=user.id, actor_name=user.name,
              action="career.interview.complete", target_type="interview_session", target_id=session.id,
              detail={"score": session.score, "answered": session.feedback["answered"]})
    db.commit()
    db.refresh(session)
    return session


@router.get("/interviews", response_model=list[schemas.InterviewSessionOut])
def list_interview_sessions(
    limit: int = 50,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Liste les sessions d'entretien du user (P41). Plus récentes d'abord. Isolation user_id."""
    profile = _get_or_404_profile(db, user.id)
    return db.query(models.InterviewSession).filter_by(career_profile_id=profile.id).order_by(
        models.InterviewSession.created_at.desc()).limit(limit).all()


@router.get("/interviews/{session_id}", response_model=schemas.InterviewSessionOut)
def get_interview_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Détail d'une session (P41) : questions + answers + feedback (si completed). Isolation user_id."""
    profile = _get_or_404_profile(db, user.id)
    session = db.query(models.InterviewSession).filter_by(id=session_id, career_profile_id=profile.id).first()
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session introuvable.")
    return session


# ─── P43 — Career Analytics ───────────────────────────────────────────────────
@router.get("/analytics/overview")
def analytics_overview(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Vue d'ensemble des KPIs Career (P43) : offres détectées/compatibles, candidatures, score moyen
    matching, taux réponse/entretien, refus, propositions, sessions entretien. Anti-invention : factuel."""
    profile = _get_or_404_profile(db, user.id)
    return career_analytics.overview(db, profile.id)


@router.get("/analytics/pipeline")
def analytics_pipeline(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Distribution des candidatures par statut (P43) — funnel pipeline."""
    profile = _get_or_404_profile(db, user.id)
    return career_analytics.pipeline(db, profile.id)


@router.get("/analytics/performance-by-platform")
def analytics_performance_platform(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Performances par plateforme (P43) : applications groupées par source_connector + taux réponse."""
    profile = _get_or_404_profile(db, user.id)
    return career_analytics.performance_by_platform(db, profile.id)


@router.get("/analytics/performance-by-cv")
def analytics_performance_cv(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Performances par version de CV (P43) : applications groupées par adapted_cv_id + statuts."""
    profile = _get_or_404_profile(db, user.id)
    return career_analytics.performance_by_cv(db, profile.id)


@router.get("/analytics/salary-trends")
def analytics_salary_trends(
    months: int = 6,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Évolution salaires + TJM (P43) : avg salary_min/max des postings candidatés, par mois.
    Anti-invention : factuel — pas de prédiction, mois sans candidature absent (pas de 0 inventé)."""
    profile = _get_or_404_profile(db, user.id)
    return career_analytics.salary_trends(db, profile.id, months=months)
