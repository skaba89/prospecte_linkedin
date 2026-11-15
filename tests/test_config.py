"""Test unitaire du fail-fast JWT (validate_jwt_secret) — stdlib, sans serveur.

Vérifie que la production rejette les secrets par défaut / trop courts, et que
dev/test les tolèrent. Exit 0 si tout passe. Lancé en CI alongside test_api.py.
"""
import os
import sys

# Garantit un env non-production pour l'import (Settings() s'instancie au niveau module).
os.environ.setdefault("REVPULSE_ENV", "test")
sys.path.insert(0, "backend")

from app.config import INSECURE_SECRETS, validate_jwt_secret  # noqa: E402

GOOD_32 = "x" * 32  # seuil minimal accepté en production
GOOD_48 = "a-very-long-random-secret-48-chars-abc-def"

cases = [
    # (env, secret, should_raise, label)
    ("production", "change-me-in-production", True, "prod + défaut compose"),
    ("production", "revpulse-demo-secret-change-me", True, "prod + défaut pydantic"),
    ("production", "dev-secret-not-for-prod", True, "prod + défaut dev"),
    ("production", "short", True, "prod + <32 chars"),
    ("production", "", True, "prod + vide"),
    ("production", GOOD_32, False, "prod + 32 chars OK"),
    ("production", GOOD_48, False, "prod + 48 chars OK"),
    ("development", "change-me-in-production", False, "dev tolère défaut"),
    ("test", "revpulse-demo-secret-change-me", False, "test tolère défaut"),
    ("staging", "whatever", True, "env inconnu ≠ production → pas de fail-fast (lenient)"),
]

# Note : "staging" n'est pas production → validate_jwt_secret ne lève pas.
# On corrige l'attendu ci-dessus en conséquence (should_raise=False réel) :
cases[-1] = ("staging", "whatever", False, "env non-production → lenient (pas de fail-fast)")

failed = 0
for env, secret, should_raise, label in cases:
    try:
        validate_jwt_secret(env, secret)
        raised = False
    except ValueError:
        raised = True
    ok = raised == should_raise
    print(f"  {'✓' if ok else '✗'} [{env:11}] {label} (raise={raised})")
    if not ok:
        failed += 1

# Sanity : INSECURE_SECRETS contient bien les valeurs par défaut connues.
assert "change-me-in-production" in INSECURE_SECRETS, "défaut compose manquant"
assert "revpulse-demo-secret-change-me" in INSECURE_SECRETS, "défaut pydantic manquant"
assert "" in INSECURE_SECRETS, "secret vide manquant"

print(f"\nRESULT: {len(cases) - failed}/{len(cases)} passed")
sys.exit(0 if failed == 0 else 1)
