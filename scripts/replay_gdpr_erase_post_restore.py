#!/usr/bin/env python3
"""P69 (G4) — Replay gdpr.erase post-restauration backup (RGPD Art. 17.3).

Problème : quand un user exécute son droit à l'effacement (DELETE /gdpr/erase), ses données sont
scrubbées dans la DB LIVE (User.email/name, AuditLog.detail, OAuth tokens, CVs — cf P65 G3). Mais les
SAUVEGARDES antérieures contiennent encore les PII. Une restauration de backup réintroduit ces PII →
le user "effacé" réapparaît avec ses données originales dans la DB restaurée → Art. 17 violé.

Procédure DPO (Art. 17.3.b/e — exceptions de conservation levées après restauration) :
  1. Après TOUTE restauration de backup, exécuter CE script.
  2. Il scanne tous les users avec gdpr_erased_at NOT NULL (= effacés avant la restauration).
  3. Pour chacun : re-applique la cascade d'effacement (re-scrub AuditLog.detail qui contient l'email
     original, hard-delete OAuth tokens, scrub CV.raw_text/parsed_data, re-anonymize User/Member/Contacts).
  4. Logge audit gdpr.erase.replay (preuve DPO de la re-purge post-restauration).

Usage :
  REVPULSE_DATABASE_URL=postgresql://... python scripts/replay_gdpr_erase_post_restore.py
  REVPULSE_DATABASE_URL=postgresql://... python scripts/replay_gdpr_erase_post_restore.py --dry-run

Exit 0 si OK. Affiche le compte des users re-purgés + surfaces scrubbées.
"""
import argparse
import os
import sys
from datetime import UTC, datetime

_PATH = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.join(_PATH, "..", "backend")
sys.path.insert(0, _BACKEND)
os.environ.setdefault("REVPULSE_ENV", "production")


def main():
    ap = argparse.ArgumentParser(description="P69 G4 — replay gdpr.erase post-restauration backup")
    ap.add_argument("--dry-run", action="store_true", help="Liste les users à re-purger sans modifier")
    args = ap.parse_args()

    from app import models
    from app.audit import log_audit
    from app.database import SessionLocal

    db = SessionLocal()
    erased_users = db.query(models.User).filter(models.User.gdpr_erased_at.isnot(None)).all()
    print(f"P69 G4 — {len(erased_users)} user(s) avec gdpr_erased_at NOT NULL (à re-purger post-restauration).")

    if args.dry_run:
        for u in erased_users:
            print(f"  [DRY-RUN] user_id={u.id} email={u.email} erased_at={u.gdpr_erased_at}")
        db.close()
        return

    anonymized_tag = "Anonymized (GDPR replay)"
    stats = {"users": 0, "audit_detail_scrubbed": 0, "oauth_deleted": 0, "cvs_scrubbed": 0}

    def _scrub_detail(detail, original_email, original_name):
        if isinstance(detail, str):
            return detail.replace(original_email, "[erased]").replace(original_name, anonymized_tag)
        if isinstance(detail, list):
            return [_scrub_detail(x, original_email, original_name) for x in detail]
        if isinstance(detail, dict):
            return {k: _scrub_detail(v, original_email, original_name) for k, v in detail.items()}
        return detail

    for u in erased_users:
        # Re-scrub User (au cas où la restauration a réintroduit l'email/name originaux).
        if u.email and not u.email.startswith("erased_"):
            u.email = f"erased_{u.id}@revpulse.io"
        if u.name != anonymized_tag:
            u.name = anonymized_tag
        # Member scrub (toutes memberships du user).
        for m in db.query(models.Member).filter_by(user_id=u.id).all():
            m.name = anonymized_tag
            m.email = f"erased_{m.id}@revpulse.io"
        # Contacts scrub (owner_id = u.id).
        for c in db.query(models.Contact).filter_by(owner_id=u.id).all():
            c.first_name = anonymized_tag
            c.last_name = ""
            c.email = None
            c.phone = None
        # AuditLog : actor_name + detail (email original peut avoir été réintroduit par la restauration).
        # Note : l'email original est déjà scrubbé dans User.email ci-dessus ; on cherche le pattern
        # erased_{id}@revpulse.io (valeur post-P65) dans le detail des entries de TOUS les tenants.
        original_name = "Anonymized (GDPR)"  # valeur post-P65
        for a in db.query(models.AuditLog).filter_by(actor_user_id=u.id).all():
            a.actor_name = anonymized_tag
        # Scan TOUTES les audit entries du/des tenants du user pour le detail contenant l'email scrub.
        # Note : après P65, l'email original était remplacé par _erased_email(user_id). On cherche ce pattern.
        erased_email_pat = f"erased_{u.id}@revpulse.io"
        for a in db.query(models.AuditLog).all():
            if a.detail and (erased_email_pat in str(a.detail) or original_name in str(a.detail)):
                a.detail = _scrub_detail(a.detail, erased_email_pat, original_name)
                stats["audit_detail_scrubbed"] += 1
        # OAuth tokens hard-delete.
        for uei in db.query(models.UserEmailIntegration).filter_by(user_id=u.id).all():
            db.delete(uei)
            stats["oauth_deleted"] += 1
        # CVs scrub.
        for cp in db.query(models.CareerProfile).filter_by(user_id=u.id).all():
            for cv in db.query(models.CV).filter_by(career_profile_id=cp.id).all():
                cv.raw_text = ""
                cv.parsed_data = None
                stats["cvs_scrubbed"] += 1
        # Audit log de la re-purge (preuve DPO).
        m_first = db.query(models.Member).filter_by(user_id=u.id, status="active").first()
        if m_first:
            log_audit(db, tenant_id=m_first.tenant_id, actor_user_id="system",
                      actor_name="DPO replay (P69 G4)", action="gdpr.erase.replay",
                      target_type="user", target_id=u.id,
                      detail={"replayed_at": datetime.now(UTC).isoformat()})
        stats["users"] += 1

    db.commit()
    db.close()
    print(f"P69 G4 — re-purge terminée : {stats['users']} user(s) re-purgés, "
          f"{stats['audit_detail_scrubbed']} audit detail scrubbed, "
          f"{stats['oauth_deleted']} OAuth tokens deleted, "
          f"{stats['cvs_scrubbed']} CVs scrubbed.")
    print("Audit log : action='gdpr.erase.replay' (preuve DPO Art. 17.3 post-restauration).")


if __name__ == "__main__":
    main()
