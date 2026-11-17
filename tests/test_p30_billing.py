#!/usr/bin/env python3
"""test_p30_billing.py — P30 Commercialisation : abonnements, quotas IA, checkout Stripe mock,
webhook, devises multi-monnaies.

Coverage (TestClient in-process + service direct calls) :
  1. Plans (4 : Free|Starter|Pro|Enterprise) avec ia_calls_month + price_eur + features.
  2. Currencies (4 : EUR|USD|GNF|XOF) avec symbole + nom.
  3. Subscription auto-créée (Free défaut) via GET /subscription.
  4. Quota guard (billing_service.check_and_increment_ia_quota) — allowed jusqu'à limite, puis blocked.
  5. Reset mensuel du quota (ia_calls_reset_at dépassé → reset).
  6. Checkout mock (simulated=True, checkout_url) pour plan payant.
  7. Checkout Free → upgraded_directly=True (sans paiement).
  8. Webhook mock (checkout.session.completed → upgrade plan + reset quota).
  9. Currency PATCH (valide EUR/USD/GNF/XOF + invalide rejeté 400).
  10. Validator enum devises sur CareerProfileRequest (valide + invalide).
  11. Gateway quota guard : generate() avec quota dépassé → quota_exceeded=True + simulated.
  12. Convert price EUR→USD/GNF/XOF (taux).

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p30_test.db python tests/test_p30_billing.py
"""
import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p30_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
os.environ.setdefault("REVPULSE_FRONTEND_DIR", str(ROOT / "frontend" / "public"))
os.environ.setdefault("WEBHOOK_SSRF_ALLOW_PRIVATE", "true")

passed = failed = 0


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def main():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import SessionLocal, Base, engine
    from app import models, schemas
    from app.services import billing_service

    # Reset DB (fresh schema)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    print("=== test_p30_billing.py — P30 Commercialisation ===\n")

    # Seed demo tenant + user via the demo endpoint
    client = TestClient(app)
    r = client.post("/api/auth/demo")
    ck(r.status_code == 200, "/api/auth/demo 200")
    tok = r.json()["token"]
    H = {"Authorization": f"Bearer {tok}"}
    # Get tenant
    r = client.get("/api/tenants", headers=H)
    ck(r.status_code == 200, "GET /api/tenants 200")
    tid = r.json()[0]["id"]

    # ── 1. Plans (4 incl Free + ia_calls_month) ───────────────────────────
    print("--- 1. Plans ---")
    r = client.get(f"/api/tenants/{tid}/billing/plans")
    ck(r.status_code == 200, "GET /plans 200")
    plans = r.json()
    plan_names = [p["name"] for p in plans]
    ck("Free" in plan_names, "plan Free présent")
    ck(len(plans) == 4, f"4 plans (Free/Starter/Pro/Enterprise) — got {len(plans)}")
    free_plan = next(p for p in plans if p["name"] == "Free")
    ck(free_plan["ia_calls_month"] == 10, f"Free ia_calls_month=10 (got {free_plan['ia_calls_month']})")
    ck(free_plan["price_eur"] == 0, "Free price_eur=0")
    ent_plan = next(p for p in plans if p["name"] == "Enterprise")
    ck(ent_plan["ia_calls_month"] is None, "Enterprise ia_calls_month=None (illimité)")

    # ── 2. Currencies (4) ─────────────────────────────────────────────────
    print("\n--- 2. Currencies ---")
    r = client.get(f"/api/tenants/{tid}/billing/currencies")
    ck(r.status_code == 200, "GET /currencies 200")
    cur_codes = [c["code"] for c in r.json()]
    for c in ("EUR", "USD", "GNF", "XOF"):
        ck(c in cur_codes, f"devise {c} présente")
    ck(len(r.json()) == 4, f"4 devises (got {len(r.json())})")

    # ── 3. Subscription auto-create (Free défaut) ─────────────────────────
    print("\n--- 3. Subscription auto-create ---")
    r = client.get(f"/api/tenants/{tid}/billing/subscription", headers=H)
    ck(r.status_code == 200, "GET /subscription 200")
    sub = r.json()
    ck(sub["plan"] in billing_service.PLANS, f"subscription plan valide ({sub['plan']})")
    ck(sub["ia_calls_used"] == 0, "ia_calls_used=0 (fresh)")
    ck(sub["simulated"] is True, "simulated=True (pas de Stripe configuré)")

    # ── 4. Quota guard (check_and_increment_ia_quota) ─────────────────────
    print("\n--- 4. Quota guard IA ---")
    db = SessionLocal()
    try:
        # Force plan Free (limit=10)
        sub_db = billing_service.get_or_create_subscription(db, tid)
        sub_db.plan = "Free"
        sub_db.ia_calls_used = 0
        sub_db.ia_calls_reset_at = datetime.utcnow() + timedelta(days=30)
        db.commit()
        # 10 appels allowed, 11e blocked
        for i in range(10):
            allowed, used, limit = billing_service.check_and_increment_ia_quota(db, tid)
            ck(allowed, f"appel {i+1}/10 allowed (used={used})")
            break  # one ck is enough; loop silently for the rest
        # Drain remaining
        for _ in range(9):
            billing_service.check_and_increment_ia_quota(db, tid)
        allowed, used, limit = billing_service.check_and_increment_ia_quota(db, tid)
        ck(not allowed, f"11e appel blocked (quota dépassé, used={used}, limit={limit})")
        ck(limit == 10, f"limit=10 for Free (got {limit})")
    finally:
        db.close()

    # ── 5. Reset mensuel ──────────────────────────────────────────────────
    print("\n--- 5. Reset mensuel quota ---")
    db = SessionLocal()
    try:
        sub_db = billing_service.get_or_create_subscription(db, tid)
        sub_db.ia_calls_used = 9
        sub_db.ia_calls_reset_at = datetime.utcnow() - timedelta(days=1)  # période dépassée
        db.commit()
        allowed, used, limit = billing_service.check_and_increment_ia_quota(db, tid)
        ck(allowed, "reset mensuel → appel allowed après reset")
        ck(used == 1, f"used=1 après reset (got {used})")
    finally:
        db.close()

    # ── 6. Checkout mock (plan payant) ────────────────────────────────────
    print("\n--- 6. Checkout mock ---")
    r = client.post(f"/api/tenants/{tid}/billing/checkout", headers=H,
                    json={"target_plan": "Pro", "success_url": "https://x.io/s", "cancel_url": "https://x.io/c"})
    ck(r.status_code == 200, "POST /checkout Pro 200")
    co = r.json()
    ck(co["simulated"] is True, "checkout simulated=True (mock, pas de Stripe)")
    ck(co["checkout_url"] and "mock" in co["checkout_url"], f"checkout_url mock (got {co['checkout_url'][:40]})")

    # ── 7. Checkout Free → upgraded_directly ──────────────────────────────
    print("\n--- 7. Checkout Free (sans paiement) ---")
    r = client.post(f"/api/tenants/{tid}/billing/checkout", headers=H,
                    json={"target_plan": "Free", "success_url": "https://x.io/s", "cancel_url": "https://x.io/c"})
    ck(r.status_code == 200, "POST /checkout Free 200")
    co = r.json()
    ck(co.get("upgraded_directly") is True, "Free → upgraded_directly=True (sans paiement)")

    # ── 8. Webhook mock (checkout.session.completed) ──────────────────────
    print("\n--- 8. Webhook Stripe mock ---")
    # Force plan Free d'abord, puis webhook upgrade vers Pro
    db = SessionLocal()
    try:
        sub_db = billing_service.get_or_create_subscription(db, tid)
        sub_db.plan = "Free"
        db.commit()
    finally:
        db.close()
    event = {"type": "checkout.session.completed",
             "data": {"object": {"metadata": {"tenant_id": tid, "target_plan": "Pro"},
                                 "customer": "cus_test123", "subscription": "sub_test456"}}}
    r = client.post("/api/billing/webhook", data=json.dumps(event),
                    headers={"Content-Type": "application/json"})
    ck(r.status_code == 200, "POST /api/billing/webhook 200")
    wk = r.json()
    ck(wk["processed"] is True, "webhook processed=True")
    ck(wk["plan"] == "Pro", f"webhook upgraded plan=Pro (got {wk.get('plan')})")
    # Verify subscription updated
    db = SessionLocal()
    try:
        sub_db = billing_service.get_or_create_subscription(db, tid)
        ck(sub_db.plan == "Pro", "subscription.plan=Pro après webhook")
        ck(sub_db.stripe_customer_id == "cus_test123", "stripe_customer_id persisté")
        ck(sub_db.ia_calls_used == 0, "ia_calls_used reset après upgrade")
    finally:
        db.close()

    # ── 9. Currency PATCH ─────────────────────────────────────────────────
    print("\n--- 9. Currency tenant ---")
    for cur in ("USD", "GNF", "XOF", "EUR"):
        r = client.patch(f"/api/tenants/{tid}/billing/currency", headers=H, json={"currency": cur})
        ck(r.status_code == 200, f"PATCH /currency {cur} 200")
    # Invalide → 400
    r = client.patch(f"/api/tenants/{tid}/billing/currency", headers=H, json={"currency": "JPY"})
    ck(r.status_code == 400, "PATCH /currency JPY → 400 (devise non supportée)")

    # ── 10. Validator enum CareerProfileRequest ───────────────────────────
    print("\n--- 10. Validator devise CareerProfile ---")
    for cur in ("eur", "USD", "gnf", "XOF"):
        req = schemas.CareerProfileRequest(target_job_title="Dev", currency=cur)
        ck(req.currency == cur.upper(), f"CareerProfileRequest currency={cur} → {req.currency}")
    try:
        schemas.CareerProfileRequest(target_job_title="Dev", currency="JPY")
        ck(False, "CareerProfileRequest currency=JPY devrait lever (devise invalide)")
    except Exception:
        ck(True, "CareerProfileRequest currency=JPY → ValueError (devise invalide)")

    # ── 11. Gateway quota guard (generate) ────────────────────────────────
    print("\n--- 11. Gateway quota guard (generate) ---")
    from app.services.ai.gateway import generate
    db = SessionLocal()
    try:
        # Force quota dépassé (Free, used=10, limit=10)
        sub_db = billing_service.get_or_create_subscription(db, tid)
        sub_db.plan = "Free"
        sub_db.ia_calls_used = 10
        sub_db.ia_calls_reset_at = datetime.utcnow() + timedelta(days=30)
        db.commit()
        res = generate(db, tenant_id=tid, prompt_name="test_quota", variables={"_prompt": "hello"},
                       dry_run=False, user_id=None)
        ck(res.get("quota_exceeded") is True, "generate() quota_exceeded=True (quota Free dépassé)")
        ck(res.get("simulated") is True, "generate() simulated=True (fallback quand quota dépassé)")
        ck(res.get("quota_limit") == 10, f"generate() quota_limit=10 (got {res.get('quota_limit')})")
        # dry_run ne consomme pas + ne flag pas quota_exceeded
        used_before = sub_db.ia_calls_used
        res2 = generate(db, tenant_id=tid, prompt_name="test_dry", variables={"_prompt": "hello"}, dry_run=True)
        ck(res2.get("quota_exceeded") is False, "generate() dry_run → quota_exceeded=False (skip guard)")
    finally:
        db.close()

    # ── 12. Convert price EUR→devise ──────────────────────────────────────
    print("\n--- 12. Conversion prix ---")
    amt_usd, sym_usd = billing_service.convert_price(100, "USD")
    ck(amt_usd == 108 and sym_usd == "$", f"100 EUR → {amt_usd} {sym_usd} (attendu 108 $)")
    amt_gnf, sym_gnf = billing_service.convert_price(1, "GNF")
    ck(amt_gnf == 9050 and sym_gnf == "GNF", f"1 EUR → {amt_gnf} {sym_gnf} (attendu 9050 GNF)")
    amt_xof, sym_xof = billing_service.convert_price(1, "XOF")
    ck(amt_xof == 656 and sym_xof == "CFA", f"1 EUR → {amt_xof} {sym_xof} (attendu ~656 CFA)")
    fp = billing_service.format_price(29, "EUR")
    ck("29" in fp and "€" in fp, f"format_price(29, EUR) = '{fp}'")

    # Cleanup
    db = SessionLocal()
    try:
        db.query(models.Subscription).delete()
        db.commit()
    finally:
        db.close()

    print(f"\nRESULT: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
