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
from sqlalchemy.orm import Session

from .. import models
from ..audit import log_audit
from ..database import get_db
from ..deps import get_tenant, require_permission

router = APIRouter(prefix="/api/tenants/{tid}/gdpr", tags=["gdpr"])


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

    return {
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
        "counts": {"contacts": len(contacts), "audit_entries": len(audit_entries),
                   "job_applications": len(job_apps)},
    }


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

    db.commit()

    # 5. Log l'action GDPR elle-même (preuve de conformité — qui a exécuté le droit, quand, sur qui)
    log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
              action="gdpr.erase", target_type="user", target_id=user_id,
              detail={"anonymized_user_id": user_id, "contacts_anonymized": len(contacts),
                      "audit_entries_scrubbed": len(audit_entries), "member_id": member.id})
    db.commit()

    return {
        "user_id": user_id,
        "erased_at": now.isoformat(),
        "executed_by": {"user_id": _perm.user_id, "name": _perm.name},
        "member_id": member.id,
        "contacts_anonymized": len(contacts),
        "audit_entries_scrubbed": len(audit_entries),
        "note": "User/Member/Contacts anonymisés + AuditLog.actor_name scrubbed. "
                "Enregistrements conservés (FK integrity + rétention SOC2 7 ans — GDPR Art. 17.3.b/e).",
    }
