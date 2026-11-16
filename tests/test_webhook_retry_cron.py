#!/usr/bin/env python3
"""test_webhook_retry_cron.py — validation statique du scheduler de retry webhooks (P19).

Couvre (par lecture des sources + introspection, pas de serveur) :
  - services/webhooks.py : start_scheduler() existe, utilise threading.Thread(daemon=True),
    nomme le thread "revpulse-webhook-scheduler", lit WEBHOOK_SCHEDULER_INTERVAL_SECONDS
    (défaut 60s), désactivé si interval <= 0, appelle deliver_pending dans la boucle,
    commit après deliver_pending + rollback sur erreur (catch large volontaire).
  - main.py : start_webhook_scheduler() appelé au startup (à côté du sequences scheduler).
  - Cohérence avec le pattern sequences.start_scheduler (stdlib threading, env-gated, daemon).
  - CI : test_webhook_retry_cron.py dans static checks.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
passed = failed = 0


def ck(got, expected, label):
    global passed, failed
    if str(got) == str(expected):
        print(f"  ✓ {label}")
        passed += 1
    else:
        print(f"  ✗ {label} — got '{got}' expected '{expected}'")
        failed += 1


print("=== test_webhook_retry_cron.py — scheduler de retry webhooks (P19) ===\n")

# ─── Source : services/webhooks.py ─────────────────────────────────────────────
svc = (ROOT / "backend" / "app" / "services" / "webhooks.py").read_text(encoding="utf-8")

print("=== services/webhooks.py : start_scheduler ===")
ck("def start_scheduler" in svc, True, "start_scheduler défini")
ck("threading.Thread(" in svc, True, "utilise threading.Thread (stdlib, pas de dep)")
ck("daemon=True" in svc, True, "thread daemon (ne bloque pas le shutdown)")
ck('name="revpulse-webhook-scheduler"' in svc, True, "thread nommé revpulse-webhook-scheduler")
ck("WEBHOOK_SCHEDULER_INTERVAL_SECONDS" in svc, True, "env WEBHOOK_SCHEDULER_INTERVAL_SECONDS")
ck('"60"' in svc, True, "défaut interval 60s (time-sensitive, plus fréquent que séquences 300s)")
ck("interval <= 0" in svc, True, "désactivé si interval <= 0 (gate env)")
ck("deliver_pending(db)" in svc, True, "appelle deliver_pending dans la boucle")
ck("db.commit()" in svc, True, "commit après deliver_pending (deliver ne commit pas)")
ck("db.rollback()" in svc, True, "rollback sur erreur (session non empoisonnée)")
ck("time.sleep(interval)" in svc, True, "sleep(interval) entre les itérations")
ck("while True:" in svc, True, "boucle infinie (daemon, arrêté avec le process)")
# catch large volontaire (le scheduler ne doit jamais crasher l'app)
ck("except Exception as e:" in svc, True, "catch large volontaire (scheduler resilient)")
ck("log.exception" in svc, True, "log.exception sur erreur (traceback capturé)")
ck("SessionLocal()" in svc, True, "SessionLocal() par itération (session fraiche, pas de stale)")

# ─── Source : main.py ──────────────────────────────────────────────────────────
main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")

print("\n=== main.py : startup hook ===")
ck("start_webhook_scheduler" in main, True, "start_webhook_scheduler importé au startup")
ck("start_webhook_scheduler()" in main, True, "start_webhook_scheduler() appelé au startup")
ck("from .services.webhooks import" in main, True, "import depuis services.webhooks")
# à côté du sequences scheduler (les deux démarrés au startup)
ck("start_scheduler()" in main, True, "sequences scheduler toujours démarré (P15 coexiste)")

# ─── Cohérence pattern sequences.start_scheduler ───────────────────────────────
seq = (ROOT / "backend" / "app" / "services" / "sequences.py").read_text(encoding="utf-8")

print("\n=== Cohérence pattern sequences.start_scheduler (P15) ===")
ck("def start_scheduler" in seq, True, "sequences.start_scheduler existe (référence pattern)")
ck("threading.Thread(" in seq, True, "sequences utilise aussi threading.Thread (cohérent)")
ck("daemon=True" in seq, True, "sequences thread aussi daemon (cohérent)")
ck("SEQUENCE_SCHEDULER_INTERVAL_SECONDS" in seq, True, "sequences env-gated (même pattern)")
ck("while True:" in seq, True, "sequences boucle infinie (même pattern)")

# ─── Introspection runtime : start_scheduler callable + interval default ───────
print("\n=== Introspection runtime : start_scheduler callable + gate env ===")
try:
    import sqlalchemy  # noqa: F401 — garde-fou : skip si deps backend non installées (CI static-checks)
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via E2E).")
else:
    try:
        sys.path.insert(0, str(ROOT / "backend"))
        from app.services import webhooks as wh_svc

        ck(callable(wh_svc.start_scheduler), True, "start_scheduler est callable")
        ck(callable(wh_svc.deliver_pending), True, "deliver_pending est callable (appelé par le scheduler)")
        # Le scheduler ne démarre pas de thread au import-time (seulement quand appelé) —
        # vérifie qu'aucun thread "revpulse-webhook-scheduler" n'existe avant l'appel.
        import threading as _t

        before = [t.name for t in _t.enumerate()]
        ck("revpulse-webhook-scheduler" not in before, True, "scheduler non démarré au import (lazy)")
        # Gate env : interval <= 0 désactive (ne démarre pas de thread)
        import os as _os

        old = _os.environ.get("WEBHOOK_SCHEDULER_INTERVAL_SECONDS")
        _os.environ["WEBHOOK_SCHEDULER_INTERVAL_SECONDS"] = "0"
        wh_svc.start_scheduler()  # ne doit PAS démarrer de thread (interval=0)
        after_zero = [t.name for t in _t.enumerate()]
        ck("revpulse-webhook-scheduler" not in after_zero, True, "interval=0 → scheduler désactivé (pas de thread)")
        # Restore + interval positif → démarre un thread daemon
        _os.environ["WEBHOOK_SCHEDULER_INTERVAL_SECONDS"] = "3600"  # 1h (ne tournera pas pendant le test)
        wh_svc.start_scheduler()
        after_pos = [t.name for t in _t.enumerate()]
        ck("revpulse-webhook-scheduler" in after_pos, True, "interval>0 → scheduler démarre (thread daemon)")
        # Cleanup : restore env (le thread daemon tournera mais à 3600s, inoffensif)
        if old is None:
            _os.environ.pop("WEBHOOK_SCHEDULER_INTERVAL_SECONDS", None)
        else:
            _os.environ["WEBHOOK_SCHEDULER_INTERVAL_SECONDS"] = old
    except Exception as e:
        ck(False, True, f"introspection runtime (erreur: {type(e).__name__}: {e})")

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
