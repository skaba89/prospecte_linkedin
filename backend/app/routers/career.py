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
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..database import get_db
from ..deps import get_current_user
from ..services import career_cv, career_referential

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
