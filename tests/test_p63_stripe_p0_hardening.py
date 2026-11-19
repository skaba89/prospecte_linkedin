#!/usr/bin/env python3
"""test_p63_stripe_p0_hardening.py — P63 Stripe P0 hardening (anti free-plan-bypass).

5 blocages P0 Stripe corrigés (audit V1.0) :
  S1 : PATCH /billing change_plan sans payer (legacy non désactivé prod).
  S2 : Frontend billing.html forge webhook checkout.session.completed client-side.
  S3 : Signature webhook CONDITIONNELLE (mock si 1 des 2 secrets manque en prod).
  S4 : Checkout fallback mock silencieux sur TOUTE exception Stripe.
  S5 : idempotency_key absent de stripe.checkout.Session.create.

Coverage :
  A. Config fail-fast (P0-S3/S4) :
    1. Settings(env=production, sans stripe_secret_key) → ValueError.
    2. Settings(env=production, sans stripe_webhook_secret) → ValueError.
    3. Settings(env=production, avec les 2 secrets) → OK (no raise).
    4. Settings(env=test, sans secrets) → OK (fail-fast prod-only).
  B. idempotency_key (P0-S5) :
    5. create_checkout_session (stripe configuré) → stripe.checkout.Session.create appelé avec idempotency_key.
    6. idempotency_key format "checkout:{tenant_id}:{plan}:{ts}".
  C. No mock fallback in prod (P0-S4) :
    7. create_checkout_session (prod + stripe exception) → HTTPException 502 (pas de mock).
    8. create_checkout_session (dev + stripe exception) → mock checkout (simulated=True, fallback conservé).
  D. PATCH /billing change_plan disabled in prod (P0-S1) :
    9. PATCH /billing en env=test → 200 (legacy conservé pour tests).
    10. PATCH /billing en env=production → 403 (désactivé).
  E. billing.html webhook forge retiré (P0-S2) :
    11. billing.html ne contient plus le bloc fetch('/api/billing/webhook') forgé client-side.
    12. billing.html ne contient plus "Webhook mock traité".
  F. Webhook signature inconditionnelle prod (P0-S3) :
    13. webhook handler en prod sans stripe_secret_key → 401 (defense-in-depth, même si config fail-fast raté).

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p63_test.db python tests/test_p63_stripe_p0_hardening.py
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p63_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
os.environ.setdefault("REVPULSE_TOKEN_ENCRYPTION_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI=")
os.environ.setdefault("REVPULSE_RATE_LIMIT_ENABLED", "false")

passed = failed = 0


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def main():
    print("=== test_p63_stripe_p0_hardening.py — P63 Stripe P0 hardening ===\n")

    # ══════════════════════════════════════════════════════════════════════
    # A. Config fail-fast (P0-S3/S4)
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. Config fail-fast (P0-S3/S4) ---")
    from app.config import Settings

    JWT_OK = "x" * 32
    FERNET_OK = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI="
    # 1. prod sans stripe_secret_key → ValueError
    try:
        Settings(env="production", jwt_secret=JWT_OK, token_encryption_key=FERNET_OK,
                 stripe_enabled=True, stripe_secret_key="", stripe_webhook_secret="whsec_x")
        ck(False, "prod sans stripe_secret_key → ValueError")
    except ValueError:
        ck(True, "prod sans stripe_secret_key → ValueError (fail-fast P0-S3)")
    # 2. prod sans stripe_webhook_secret → ValueError
    try:
        Settings(env="production", jwt_secret=JWT_OK, token_encryption_key=FERNET_OK,
                 stripe_enabled=True, stripe_secret_key="sk_live_x", stripe_webhook_secret="")
        ck(False, "prod sans stripe_webhook_secret → ValueError")
    except ValueError:
        ck(True, "prod sans stripe_webhook_secret → ValueError (fail-fast P0-S3)")
    # 3. prod avec les 2 secrets → OK (cors_origins explicite requis depuis P64 CORS fail-fast)
    try:
        s = Settings(env="production", jwt_secret=JWT_OK, token_encryption_key=FERNET_OK,
                     stripe_secret_key="sk_live_x", stripe_webhook_secret="whsec_x",
                     cors_origins="https://app.io")
        ck(s.is_production and s.stripe_secret_key == "sk_live_x", "prod avec 2 secrets → OK (no raise)")
    except ValueError:
        ck(False, "prod avec 2 secrets → OK (no raise)")
    # 4. test env sans secrets → OK
    try:
        s = Settings(env="test", jwt_secret=JWT_OK, stripe_secret_key="", stripe_webhook_secret="")
        ck(not s.is_production, "test env sans secrets → OK (fail-fast prod-only)")
    except ValueError:
        ck(False, "test env sans secrets → OK")
    # 5. P70-fix : prod + stripe_enabled=False + pas de keys → OK (Docker local sans Stripe).
    #    Le fail-fast Stripe est skipé (opt-out explicite) → le backend boote en mock mode.
    try:
        s = Settings(env="production", jwt_secret=JWT_OK, token_encryption_key=FERNET_OK,
                     stripe_enabled=False, stripe_secret_key="", stripe_webhook_secret="",
                     cors_origins="http://localhost:8080")
        ck(s.is_production and not s.stripe_enabled,
           "prod + stripe_enabled=False + no keys → OK (Docker local mock mode, P70-fix)")
    except ValueError:
        ck(False, "prod + stripe_enabled=False + no keys → OK (Docker local)")

    # ══════════════════════════════════════════════════════════════════════
    # B. idempotency_key (P0-S5)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. idempotency_key (P0-S5) ---")
    from app import models as M
    from app.database import Base, get_db
    from app.services import billing_service
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(os.environ["REVPULSE_DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)
    test_db = Sess()

    for tbl in [M.StripeEventLog, M.Subscription, M.Member, M.User, M.Tenant]:
        test_db.query(tbl).delete()
    test_db.commit()
    tenant = M.Tenant(id="tnt_p63", name="Corp P63", plan="Free", owner_id="usr_p63",
                      sso_enabled=False, retention_days=2555)
    owner = M.User(id="usr_p63", email="owner@p63.io", name="Owner P63", password_hash="h", locale="fr")
    test_db.add_all([owner, tenant]); test_db.commit()

    # Mock stripe module
    fake_stripe = MagicMock()
    fake_session = MagicMock()
    fake_session.url = "https://checkout.stripe.com/c/pay/cs_test_123"
    fake_session.id = "cs_test_123"
    fake_stripe.checkout.Session.create.return_value = fake_session
    sys.modules["stripe"] = fake_stripe

    # Configure settings pour activer le path stripe réel (dev env + stripe_secret_key set)
    billing_service.settings.stripe_secret_key = "sk_test_x"
    billing_service.settings.stripe_webhook_secret = "whsec_x"
    billing_service.settings.env = "test"
    try:
        result = billing_service.create_checkout_session(test_db, "tnt_p63", "Pro",
                                                         "https://app/success", "https://app/cancel")
        ck(result["simulated"] is False, "create_checkout (stripe réel) → simulated=False")
        # Assert stripe.checkout.Session.create called avec idempotency_key
        _, kwargs = fake_stripe.checkout.Session.create.call_args
        ck("idempotency_key" in kwargs, "stripe.checkout.Session.create appelé avec idempotency_key (P0-S5)")
        ik = kwargs.get("idempotency_key", "")
        ck(ik.startswith("checkout:tnt_p63:Pro:"), f"idempotency_key format 'checkout:{{tid}}:{{plan}}:{{ts}}' (got {ik[:25]})")
    finally:
        billing_service.settings.stripe_secret_key = ""
        billing_service.settings.stripe_webhook_secret = ""

    # ══════════════════════════════════════════════════════════════════════
    # C. No mock fallback in prod (P0-S4)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. No mock fallback in prod (P0-S4) ---")
    from fastapi import HTTPException

    # Mock stripe qui raise
    fake_stripe_err = MagicMock()
    fake_stripe_err.checkout.Session.create.side_effect = Exception("Stripe API timeout")
    sys.modules["stripe"] = fake_stripe_err

    # Prod + stripe exception → HTTPException 502 (pas de mock fallback)
    billing_service.settings.stripe_secret_key = "sk_live_x"
    billing_service.settings.env = "production"
    try:
        try:
            billing_service.create_checkout_session(test_db, "tnt_p63", "Pro",
                                                   "https://app/success", "https://app/cancel")
            ck(False, "prod + stripe exception → HTTPException 502")
        except HTTPException as e:
            ck(e.status_code == 502, f"prod + stripe exception → HTTPException 502 (got {e.status_code}) (P0-S4)")
    finally:
        billing_service.settings.env = "test"

    # Dev + stripe exception → mock checkout (fallback conservé en dev)
    billing_service.settings.stripe_secret_key = "sk_test_x"
    billing_service.settings.env = "test"
    try:
        result = billing_service.create_checkout_session(test_db, "tnt_p63", "Pro",
                                                         "https://app/success", "https://app/cancel")
        ck(result.get("simulated") is True, "dev + stripe exception → mock checkout (simulated=True, fallback conservé)")
    finally:
        billing_service.settings.stripe_secret_key = ""
    sys.modules.pop("stripe", None)

    # ══════════════════════════════════════════════════════════════════════
    # D. PATCH /billing change_plan disabled in prod (P0-S1)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- D. PATCH /billing change_plan (P0-S1) ---")
    def _override_db():
        yield test_db
    from app.main import app
    app.dependency_overrides[get_db] = _override_db
    from fastapi.testclient import TestClient
    client = TestClient(app)
    from app.security import create_access_token
    mem = M.Member(id="mem_p63", tenant_id="tnt_p63", user_id="usr_p63",
                   email="owner@p63.io", name="Owner P63", role="Owner", status="active")
    test_db.add(mem); test_db.commit()
    H = {"Authorization": f"Bearer {create_access_token('usr_p63')}"}

    # Dev env → PATCH works (legacy conservé)
    from app.config import settings as cfg
    cfg.env = "test"
    r = client.patch("/api/tenants/tnt_p63/billing", headers=H, json={"plan": "Starter"})
    ck(r.status_code == 200, f"PATCH /billing en dev → 200 (legacy conservé) (got {r.status_code})")
    # reset plan to Free for next test
    t = test_db.query(M.Tenant).filter_by(id="tnt_p63").first()
    t.plan = "Free"; test_db.commit()

    # Prod env → PATCH 403
    cfg.env = "production"
    try:
        r = client.patch("/api/tenants/tnt_p63/billing", headers=H, json={"plan": "Pro"})
        ck(r.status_code == 403, f"PATCH /billing en prod → 403 (désactivé P0-S1) (got {r.status_code})")
    finally:
        cfg.env = "test"

    # ══════════════════════════════════════════════════════════════════════
    # E. billing.html webhook forge retiré (P0-S2)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- E. billing.html webhook forge retiré (P0-S2) ---")
    html = (ROOT / "frontend" / "public" / "billing.html").read_text()
    ck("checkout.session.completed" not in html, "billing.html : plus de forge 'checkout.session.completed' (P0-S2)")
    ck("Webhook mock traité" not in html, "billing.html : plus de toast 'Webhook mock traité' (P0-S2)")
    ck("/api/billing/webhook', {method:'POST'" not in html, "billing.html : plus de fetch POST /api/billing/webhook forgé (P0-S2)")
    ck("Mode mock — checkout simulé" in html, "billing.html : toast mock conservé (info only, sans forge)")

    # ══════════════════════════════════════════════════════════════════════
    # F. Webhook signature inconditionnelle prod (P0-S3) — defense-in-depth
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- F. Webhook signature inconditionnelle prod (P0-S3) ---")
    cfg.env = "production"
    cfg.stripe_enabled = True  # P70-fix : explicitement True pour tester le guard defense-in-depth
    cfg.stripe_webhook_secret = ""  # simulate misconfig (config fail-fast would block boot, but defense-in-depth here)
    try:
        r = client.post("/api/billing/webhook", headers=H, json={"type": "checkout.session.completed"})
        ck(r.status_code == 401, f"webhook prod sans secrets → 401 (defense-in-depth P0-S3) (got {r.status_code})")
    finally:
        cfg.env = "test"
        cfg.stripe_enabled = True
        cfg.stripe_webhook_secret = ""

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
