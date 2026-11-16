#!/usr/bin/env bash
# Audit advisory des dépendances : pip-audit (backend) + npm audit (frontend).
#
# Usage : ./scripts/audit_deps.sh           # advisory (exit 0 même si vulns)
#         ./scripts/audit_deps.sh --strict  # exit 1 si vuln (CI gating optionnel)
#
# npm audit nécessite package-lock.json — skip gracieux si absent.
# pip-audit nécessite le package pip-audit — message d'installe sinon.
set -euo pipefail
cd "$(dirname "$0")/.."
STRICT="${1:-}"

echo "=== pip-audit (backend/requirements.txt) ==="
if command -v pip-audit >/dev/null 2>&1; then
  if [ "$STRICT" = "--strict" ]; then
    pip-audit -r backend/requirements.txt
  else
    pip-audit -r backend/requirements.txt || echo "  (vulnérabilités signalées — mode advisory, exit 0)"
  fi
else
  echo "  pip-audit absent — installer : pip install pip-audit"
fi

echo ""; echo "=== npm audit (frontend) ==="
if [ ! -f frontend/package-lock.json ]; then
  echo "  skip : pas de package-lock.json (générer via 'npm install --package-lock-only' dans frontend/)"
elif command -v npm >/dev/null 2>&1; then
  if [ "$STRICT" = "--strict" ]; then
    ( cd frontend && npm audit --audit-level=high )
  else
    ( cd frontend && npm audit --audit-level=high ) || echo "  (vulnérabilités signalées — mode advisory, exit 0)"
  fi
else
  echo "  npm absent — skip"
fi
echo "✓ Audit deps terminé (advisory par défaut ; --strict pour fail)."
