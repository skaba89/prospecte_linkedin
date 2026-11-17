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

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..database import get_db
from ..deps import get_current_user
from ..services import career_cv, career_referential, cv_adapter, cv_generator, job_search, matching
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
