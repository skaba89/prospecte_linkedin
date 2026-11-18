#!/usr/bin/env python3
"""test_p44_stripe_real.py — P44 Stripe réel (sandbox + webhook security + events + idempotency).

Coverage (mock HTTP/lib — AUCUN réseau Stripe externe en CI) :
  1. Config : stripe_publishable_key + stripe_api_version présents.
  2. StripeEventLog model + migration 0028 (table stripe_event_log).
  3. _stripe_ts_to_dt : unix timestamp → datetime UTC.
  4. handle_webhook_event checkout.session.completed → upgrade plan + reset quota + stripe IDs + StripeEventLog.
  5. handle_webhook_event customer.subscription.updated → sync status + current_period_end depuis Stripe.
  6. handle_webhook_event customer.subscription.deleted → status=canceled + plan=Free.
  7. handle_webhook_event invoice.paid → status=active.
  8. handle_webhook_event invoice.payment_failed → status=past_due.
  9. Idempotency : replay même event_id → duplicate, PAS de retraitement (anti double upgrade).
 10. Webhook route security : is_production + pas de stripe_webhook_secret → 401 (anti free-plan-bypass).
 11. Webhook route non-200 sur échec : event avec metadata manquante → 500 (Stripe retryera).
 12. Webhook route signature verify : mock stripe.Webhook.construct_event → real path (pas de mock JSON).
 13. Checkout fix : customer_email utilise owner email (PAS stripe_customer_id).

Sécurité P44 : signature OBLIGATOIRE en prod, idempotency anti-replay, non-200 sur échec (Stripe retry),
fix bug customer_email=stripe_customer_id, 4 events webhook complets (updated/paid/payment_failed).

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p44_test.db python tests/test_p44_stripe_real.py
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p44_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")

passed = failed = 0


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def main():
    from app import models as M
    from app.config import settings
    from app.database import Base
    from app.services import billing_service as B
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(os.environ["REVPULSE_DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    db = S()

    # Clean state
    db.query(M.StripeEventLog).delete()
    db.query(M.Subscription).delete()
    db.query(M.Tenant).delete()
    db.query(M.User).delete()
    db.commit()

    # Seed user + tenant (pour events customer-based)
    u = M.User(id="usr_p44", email="p44@test.com", name="P44", password_hash="h")
    t = M.Tenant(id="tnt_p44", name="P44 Tenant", plan="free", owner_id="usr_p44", currency="EUR")
    db.add_all([u, t]); db.commit()

    print("=== test_p44_stripe_real.py — P44 Stripe réel (sandbox + webhook + idempotency) ===\n")

    # ── 1. Config ────────────────────────────────────────────────────────
    print("--- 1. Config (stripe_publishable_key + stripe_api_version) ---")
    ck(isinstance(settings.stripe_publishable_key, str), "stripe_publishable_key présent (str)")
    ck(bool(settings.stripe_api_version), f"stripe_api_version présent ({settings.stripe_api_version})")

    # ── 2. StripeEventLog model + migration ──────────────────────────────
    print("\n--- 2. StripeEventLog model + table ---")
    ck(hasattr(M, "StripeEventLog"), "model StripeEventLog défini")
    ck(M.StripeEventLog.__tablename__ == "stripe_event_log", "tablename=stripe_event_log")
    # Table créée (Base.metadata.create_all)
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(engine)
    ck("stripe_event_log" in insp.get_table_names(), "table stripe_event_log créée en DB")
    # Colonnes
    cols = {c["name"] for c in insp.get_columns("stripe_event_log")}
    ck({"id", "event_type", "processed_at", "result"} <= cols, f"colonnes présentes ({cols})")

    # ── 3. _stripe_ts_to_dt ──────────────────────────────────────────────
    print("\n--- 3. _stripe_ts_to_dt (unix → datetime) ---")
    # 2026-11-18 12:00:00 UTC = 1773614400
    dt = B._stripe_ts_to_dt(1773614400)
    ck(dt is not None and dt.year == 2026, f"timestamp valide → datetime 2026 (got {dt})")
    ck(B._stripe_ts_to_dt(None) is None, "None → None")
    ck(B._stripe_ts_to_dt(0) is None, "0 → None (falsy)")
    ck(B._stripe_ts_to_dt("notanumber") is None, "string invalide → None (best-effort)")

    # ── 4. checkout.session.completed → upgrade + StripeEventLog ─────────
    print("\n--- 4. checkout.session.completed → upgrade + event log ---")
    evt_checkout = {
        "id": "evt_checkout_001", "type": "checkout.session.completed",
        "data": {"object": {
            "metadata": {"tenant_id": "tnt_p44", "target_plan": "Pro"},
            "customer": "cus_abc", "subscription": "sub_xyz",
        }},
    }
    result = B.handle_webhook_event(db, evt_checkout)
    ck(result["processed"] is True, "checkout → processed=True")
    ck(result["plan"] == "Pro", "plan=Pro")
    sub = db.query(M.Subscription).filter_by(tenant_id="tnt_p44").first()
    ck(sub is not None, "Subscription créée")
    ck(sub.plan == "Pro", "sub.plan=Pro")
    ck(sub.status == "active", "sub.status=active")
    ck(sub.stripe_customer_id == "cus_abc", "stripe_customer_id=cus_abc")
    ck(sub.stripe_subscription_id == "sub_xyz", "stripe_subscription_id=sub_xyz")
    ck(sub.ia_calls_used == 0, "quota reset (ia_calls_used=0)")
    # StripeEventLog enregistré
    log = db.query(M.StripeEventLog).filter_by(id="evt_checkout_001").first()
    ck(log is not None, "StripeEventLog row créée")
    ck(log.event_type == "checkout.session.completed", "log.event_type correct")
    ck(log.result == "ok", "log.result=ok")

    # ── 5. customer.subscription.updated → sync status + current_period_end ─
    print("\n--- 5. customer.subscription.updated → sync status + period_end ---")
    evt_upd = {
        "id": "evt_sub_updated_002", "type": "customer.subscription.updated",
        "data": {"object": {
            "customer": "cus_abc", "status": "past_due",
            "current_period_end": 1773614400, "id": "sub_xyz",
        }},
    }
    result = B.handle_webhook_event(db, evt_upd)
    ck(result["processed"] is True, "updated → processed=True")
    db.refresh(sub)
    ck(sub.status == "past_due", f"sub.status sync past_due (got {sub.status})")
    ck(sub.current_period_end is not None and sub.current_period_end.year == 2026,
       f"current_period_end depuis Stripe (got {sub.current_period_end})")

    # ── 6. customer.subscription.deleted → canceled + Free ───────────────
    print("\n--- 6. customer.subscription.deleted → canceled + Free ---")
    # Reset sub à Pro d'abord
    sub.status = "active"; sub.plan = "Pro"; db.commit()
    evt_del = {
        "id": "evt_sub_deleted_003", "type": "customer.subscription.deleted",
        "data": {"object": {"customer": "cus_abc"}},
    }
    result = B.handle_webhook_event(db, evt_del)
    ck(result["processed"] is True, "deleted → processed=True")
    db.refresh(sub)
    ck(sub.status == "canceled", "sub.status=canceled")
    ck(sub.plan == "Free", "sub.plan=Free")

    # ── 7. invoice.paid → active ─────────────────────────────────────────
    print("\n--- 7. invoice.paid → active ---")
    sub.status = "past_due"; db.commit()  # simulate past_due before payment
    evt_paid = {
        "id": "evt_invoice_paid_004", "type": "invoice.paid",
        "data": {"object": {"customer": "cus_abc", "period_end": 1773614400, "created": 1773528000}},
    }
    result = B.handle_webhook_event(db, evt_paid)
    ck(result["processed"] is True, "invoice.paid → processed=True")
    db.refresh(sub)
    ck(sub.status == "active", "sub.status=active (paiement réussi)")

    # ── 8. invoice.payment_failed → past_due ─────────────────────────────
    print("\n--- 8. invoice.payment_failed → past_due ---")
    sub.status = "active"; db.commit()
    evt_fail = {
        "id": "evt_invoice_failed_005", "type": "invoice.payment_failed",
        "data": {"object": {"customer": "cus_abc"}},
    }
    result = B.handle_webhook_event(db, evt_fail)
    ck(result["processed"] is True, "payment_failed → processed=True")
    db.refresh(sub)
    ck(sub.status == "past_due", "sub.status=past_due (paiement échoué)")

    # ── 9. Idempotency (replay même event_id → duplicate, no reprocess) ──
    print("\n--- 9. Idempotency (replay event → duplicate) ---")
    # Rejouer evt_checkout_001 (déjà traité au test 4)
    sub.ia_calls_used = 5; db.commit()  # marker pour vérifier non-retraitement
    result = B.handle_webhook_event(db, evt_checkout)
    ck(result.get("duplicate") is True, "replay → duplicate=True")
    ck(result.get("processed") is False, "replay → processed=False (pas retraité)")
    db.refresh(sub)
    ck(sub.ia_calls_used == 5, "quota NON reset au replay (idempotent — ia_calls_used reste 5)")

    # ── 10. Webhook route security (prod + no secret → 401) ──────────────
    print("\n--- 10. Webhook route security (prod + no secret → 401) ---")
    from app.main import stripe_webhook
    from fastapi import HTTPException
    # Mock Request
    mock_req = SimpleNamespace(
        body=AsyncMock(return_value=b'{"id":"evt_x","type":"checkout.session.completed","data":{"object":{}}}'),
        headers={"stripe-signature": ""},
    )
    with patch.object(settings, "env", "production"), \
         patch.object(settings, "stripe_webhook_secret", ""):
        try:
            import asyncio
            asyncio.get_event_loop().run_until_complete(stripe_webhook(mock_req, db))
            ck(False, "prod + no secret devrait lever 401")
        except HTTPException as e:
            ck(e.status_code == 401, f"prod + no secret → 401 (got {e.status_code})")
        except Exception as e:
            ck(False, f"exception inattendue : {type(e).__name__}: {e}")

    # ── 11. Webhook route non-200 sur échec (metadata manquante → 500) ───
    print("\n--- 11. Webhook route non-200 sur échec (metadata missing → 500) ---")
    mock_req2 = SimpleNamespace(
        body=AsyncMock(return_value=json.dumps({
            "id": "evt_bad_meta", "type": "checkout.session.completed",
            "data": {"object": {}},  # pas de metadata
        }).encode()),
        headers={"stripe-signature": ""},
    )
    # Nettoyer l'event log pour ce test
    db.query(M.StripeEventLog).filter_by(id="evt_bad_meta").delete(); db.commit()
    with patch.object(settings, "env", "test"), \
         patch.object(settings, "stripe_webhook_secret", ""), \
         patch.object(settings, "stripe_secret_key", ""):
        try:
            import asyncio
            asyncio.get_event_loop().run_until_complete(stripe_webhook(mock_req2, db))
            ck(False, "metadata missing devrait lever 500")
        except HTTPException as e:
            ck(e.status_code == 500, f"metadata missing → 500 (Stripe retryera) (got {e.status_code})")
        except Exception as e:
            ck(False, f"exception inattendue : {type(e).__name__}: {e}")

    # ── 12. Webhook route signature verify (mock construct_event → real path) ─
    print("\n--- 12. Webhook route signature verify (real path) ---")
    # Mock le module stripe : construct_event retourne un event dict
    fake_stripe = MagicMock()
    fake_stripe.Webhook.construct_event.return_value = SimpleNamespace(
        to_dict=lambda: {"id": "evt_sig_001", "type": "checkout.session.completed",
                         "data": {"object": {"metadata": {"tenant_id": "tnt_p44", "target_plan": "Starter"},
                                             "customer": "cus_sig", "subscription": "sub_sig"}}}
    )
    mock_req3 = SimpleNamespace(
        body=AsyncMock(return_value=b"raw_stripe_payload"),
        headers={"stripe-signature": "t=123,v1=fakesig"},
    )
    db.query(M.Subscription).filter_by(tenant_id="tnt_p44").delete(); db.commit()
    with patch.object(settings, "stripe_webhook_secret", "whsec_test"), \
         patch.object(settings, "stripe_secret_key", "sk_test"), \
         patch.object(settings, "stripe_api_version", "2024-06-20"), \
         patch.dict("sys.modules", {"stripe": fake_stripe}):
        try:
            import asyncio
            res = asyncio.get_event_loop().run_until_complete(stripe_webhook(mock_req3, db))
            ck(res.get("processed") is True, "signature verify → event traité (real path)")
            ck(res.get("plan") == "Starter", "plan=Starter via signature verify")
            ck(fake_stripe.api_key == "sk_test", "stripe.api_key set")
            ck(fake_stripe.api_version == "2024-06-20", "stripe.api_version pinned")
        except Exception as e:
            ck(False, f"signature verify path échoué : {type(e).__name__}: {e}")

    # ── 13. Checkout fix (customer_email utilise owner email) ────────────
    print("\n--- 13. Checkout fix (customer_email = owner email, PAS stripe_customer_id) ---")
    # Mock stripe.checkout.Session.create → capture kwargs
    captured = {}
    fake_session = SimpleNamespace(url="https://checkout.stripe.com/c/pay_xyz", id="cs_test_123")
    fake_stripe2 = MagicMock()
    fake_stripe2.checkout.Session.create.side_effect = lambda **kw: captured.update(kw) or fake_session
    # Sub sans stripe_customer_id → doit utiliser customer_email=owner email
    db.query(M.Subscription).filter_by(tenant_id="tnt_p44").delete(); db.commit()
    with patch.object(settings, "stripe_secret_key", "sk_test"), \
         patch.object(settings, "stripe_api_version", "2024-06-20"), \
         patch.dict("sys.modules", {"stripe": fake_stripe2}):
        res = B.create_checkout_session(db, "tnt_p44", "Pro", "https://ok", "https://cancel")
    ck(res["simulated"] is False, "checkout réel → simulated=False")
    ck(res["checkout_url"] == "https://checkout.stripe.com/c/pay_xyz", "checkout_url retournée")
    ck("customer_email" in captured, "customer_email utilisé (PAS customer= car pas de stripe_customer_id)")
    ck(captured["customer_email"] == "p44@test.com", f"customer_email=owner email (got {captured.get('customer_email')})")
    ck("customer" not in captured, "customer= NON utilisé (pas de stripe_customer_id)")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
