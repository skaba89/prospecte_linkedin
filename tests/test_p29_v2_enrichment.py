#!/usr/bin/env python3
"""test_p29_v2_enrichment.py — P29-V2 enrichissement prospects Business (Hunter.io Email Finder API).

Coverage (mock HTTP via unittest.mock — AUCUN réseau externe en CI) :
  1. not_configured : HUNTER_API_KEY absent → enrich_prospect status "not_configured" + simulated=True.
  2. enriched : mock Hunter API retourne email + confidence ≥ 70 → status "enriched" + email + position.
  3. not_found : mock Hunter retourne email avec confidence < seuil → status "not_found" + email=None.
  4. not_found (no email) : mock Hunter retourne aucun email → not_found.
  5. error : mock Hunter API HTTP 500 → status "error".
  6. error : domain/first_name/last_name manquants → error.
  7. rate-limit : 100 enrichissements/jour → error (anti-abus).
  8. _extract_domain : website → domain (fallback si company.domain vide).
  9. is_hunter_configured : True avec key, False sans key.
 10. CONFIDENCE_THRESHOLD = 70 + DAILY_ENRICH_LIMIT = 100 (constantes anti-fake/anti-abus).

Anti-fake (P29 invariant) : JAMAIS d'email inventé — not_found retourne email=None (le commercial saisit
manuellement). simulated=True quand not_configured (pas d'appel API).

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p29_test.db python tests/test_p29_v2_enrichment.py
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p29_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")

passed = failed = 0


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def _mock_resp(status_code: int, body: bytes):
    """Mock context manager pour urllib.request.urlopen."""
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.status = status_code
    m.read = MagicMock(return_value=body)
    return m


def main():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import models as M
    from app.database import Base
    from app.services import prospect_enrichment as pe
    from app.config import settings

    engine = create_engine(os.environ["REVPULSE_DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    db = S()

    # Clean state
    for tbl in [M.AuditLog, M.Contact, M.Company, M.Tenant, M.User]:
        db.query(tbl).delete()
    db.commit()

    # Seed tenant + user + company + contact (User first — owner_id FK)
    u = M.User(id="usr_p29", email="p29@test.com", name="Sales", password_hash="h")
    db.add(u); db.commit()
    t = M.Tenant(id="tnt_p29", name="Test", plan="Pro", owner_id="usr_p29")
    cmp = M.Company(id="cmp_p29", tenant_id="tnt_p29", name="TechCorp",
                    domain="techcorp.com", website="https://www.techcorp.com", size="51-200")
    c = M.Contact(id="ctc_p29", tenant_id="tnt_p29", company_id="cmp_p29",
                  first_name="Jean", last_name="Dupont", email="jean@old.com",
                  title="Dev", status="new")
    db.add_all([t, cmp, c])
    db.commit()

    print("=== test_p29_v2_enrichment.py — P29-V2 Hunter.io Email Finder ===\n")

    # ── 1. not_configured (HUNTER_API_KEY absent) ─────────────────────────
    print("--- 1. not_configured (Hunter non configuré) ---")
    with patch.object(settings, "hunter_api_key", ""):
        ck(pe.is_hunter_configured() is False, "is_hunter_configured=False sans key")
        res = pe.enrich_prospect("techcorp.com", "Jean", "Dupont", db, "tnt_p29")
        ck(res.status == "not_configured", f"status=not_configured (got {res.status})")
        ck(res.simulated is True, "simulated=True (pas d'appel API)")
        ck(res.email is None, "email=None (anti-fake : JAMAIS d'email inventé)")
        ck("HUNTER_API_KEY" in (res.error or ""), "error mentionne API key absent")

    # ── 2. enriched (mock Hunter API retourne email + confidence ≥ 70) ────
    print("\n--- 2. enriched (email trouvé, confidence=85) ---")
    hunter_body = json.dumps({
        "data": {
            "email": "jean.dupont@techcorp.com",
            "score": 85,  # confidence ≥ seuil 70
            "position": "VP Engineering",
            "company": "TechCorp",
            "linkedin": "https://linkedin.com/in/jeandupont",
        }
    }).encode()
    with patch.object(settings, "hunter_api_key", "test_hkey"), \
         patch("urllib.request.urlopen", return_value=_mock_resp(200, hunter_body)):
        res = pe.enrich_prospect("techcorp.com", "Jean", "Dupont", db, "tnt_p29")
    ck(res.status == "enriched", f"status=enriched (got {res.status})")
    ck(res.email == "jean.dupont@techcorp.com", f"email trouvé (got {res.email})")
    ck(res.confidence == 85, f"confidence=85 (got {res.confidence})")
    ck(res.position == "VP Engineering", f"position retournée (got {res.position})")
    ck(res.company == "TechCorp", "company retournée")
    ck(res.linkedin_url == "https://linkedin.com/in/jeandupont", "linkedin retourné")
    ck(res.simulated is False, "simulated=False (appel API réel)")

    # ── 3. not_found (confidence < seuil 70) ──────────────────────────────
    print("\n--- 3. not_found (confidence=50 < seuil 70) ---")
    hunter_low = json.dumps({
        "data": {"email": "j.dupont@techcorp.com", "score": 50, "position": "Dev"}
    }).encode()
    with patch.object(settings, "hunter_api_key", "test_hkey"), \
         patch("urllib.request.urlopen", return_value=_mock_resp(200, hunter_low)):
        res = pe.enrich_prospect("techcorp.com", "Jean", "Dupont", db, "tnt_p29")
    ck(res.status == "not_found", f"status=not_found (got {res.status})")
    ck(res.email is None, "email=None (confidence insuffisante — anti-fake)")
    ck(res.confidence == 50, f"confidence=50 retournée (got {res.confidence})")
    ck("confidence" in (res.error or ""), "error mentionne confidence/seuil")

    # ── 4. not_found (aucun email retourné par Hunter) ────────────────────
    print("\n--- 4. not_found (Hunter retourne aucun email) ---")
    hunter_none = json.dumps({"data": {"email": None, "score": 0}}).encode()
    with patch.object(settings, "hunter_api_key", "test_hkey"), \
         patch("urllib.request.urlopen", return_value=_mock_resp(200, hunter_none)):
        res = pe.enrich_prospect("techcorp.com", "Jean", "Dupont", db, "tnt_p29")
    ck(res.status == "not_found", f"status=not_found (got {res.status})")
    ck(res.email is None, "email=None (aucun email trouvé)")

    # ── 5. error (Hunter API HTTP 500) ───────────────────────────────────
    print("\n--- 5. error (Hunter API HTTP 500) ---")
    import urllib.error
    err_500 = urllib.error.HTTPError(url="https://api.hunter.io/v2/email-finder",
                                     code=500, msg="Server Error", hdrs=None, fp=None)
    with patch.object(settings, "hunter_api_key", "test_hkey"), \
         patch("urllib.request.urlopen", side_effect=err_500):
        res = pe.enrich_prospect("techcorp.com", "Jean", "Dupont", db, "tnt_p29")
    ck(res.status == "error", f"status=error (got {res.status})")
    ck("HTTP 500" in (res.error or ""), "error mentionne HTTP 500")

    # ── 6. error (params manquants) ──────────────────────────────────────
    print("\n--- 6. error (domain/first_name/last_name manquants) ---")
    with patch.object(settings, "hunter_api_key", "test_hkey"):
        res = pe.enrich_prospect("", "Jean", "Dupont")
        ck(res.status == "error", f"domain vide → error (got {res.status})")
        res = pe.enrich_prospect("techcorp.com", "", "Dupont")
        ck(res.status == "error", "first_name vide → error")
        res = pe.enrich_prospect("techcorp.com", "Jean", "")
        ck(res.status == "error", "last_name vide → error")

    # ── 7. rate-limit (anti-abus 100/jour) ───────────────────────────────
    print("\n--- 7. rate-limit (100 enrichissements/jour — anti-abus) ---")
    ck(pe.DAILY_ENRICH_LIMIT == 100, f"DAILY_ENRICH_LIMIT=100 (got {pe.DAILY_ENRICH_LIMIT})")
    # Simuler 100 enrichissements aujourd'hui (audit logs)
    for i in range(100):
        db.add(M.AuditLog(tenant_id="tnt_p29", actor_user_id="usr_p29", action="crm.contact.enrich",
                          target_type="contact", target_id=f"ctc_{i}", detail="{}",
                          created_at=datetime.utcnow()))
    db.commit()
    with patch.object(settings, "hunter_api_key", "test_hkey"):
        res = pe.enrich_prospect("techcorp.com", "Jean", "Dupont", db, "tnt_p29")
    ck(res.status == "error", f"rate-limit → status=error (got {res.status})")
    ck("Rate-limit" in (res.error or ""), "error mentionne rate-limit (anti-abus)")

    # ── 8. _extract_domain (website → domain) ────────────────────────────
    print("\n--- 8. _extract_domain (website → domain) ---")
    from app.routers.crm import _extract_domain
    ck(_extract_domain("https://www.techcorp.com/about") == "techcorp.com",
       f"https://www.techcorp.com/about → techcorp.com (got {_extract_domain('https://www.techcorp.com/about')})")
    ck(_extract_domain("http://example.com") == "example.com", "http://example.com → example.com")
    ck(_extract_domain("www.acme.io/page") == "acme.io", "www.acme.io/page → acme.io")
    ck(_extract_domain("acme.io") == "acme.io", "acme.io (no scheme) → acme.io")
    ck(_extract_domain("") == "", "website vide → domaine vide")

    # ── 9. is_hunter_configured ──────────────────────────────────────────
    print("\n--- 9. is_hunter_configured ---")
    with patch.object(settings, "hunter_api_key", ""):
        ck(pe.is_hunter_configured() is False, "False sans key")
    with patch.object(settings, "hunter_api_key", "real_key_abc"):
        ck(pe.is_hunter_configured() is True, "True avec key")

    # ── 10. constantes anti-fake/anti-abus ───────────────────────────────
    print("\n--- 10. constantes (seuil confiance + rate-limit) ---")
    ck(pe.CONFIDENCE_THRESHOLD == 70, f"CONFIDENCE_THRESHOLD=70 (got {pe.CONFIDENCE_THRESHOLD})")
    ck(pe.DAILY_ENRICH_LIMIT == 100, f"DAILY_ENRICH_LIMIT=100 (got {pe.DAILY_ENRICH_LIMIT})")
    # Anti-fake invariant : not_configured ne retourne JAMAIS d'email
    with patch.object(settings, "hunter_api_key", ""):
        res = pe.enrich_prospect("techcorp.com", "Jean", "Dupont")
        ck(res.email is None, "not_configured → email=None (anti-fake : JAMAIS inventé)")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
