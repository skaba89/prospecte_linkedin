#!/usr/bin/env bash
# Audit des dépendances : pip-audit (backend) + npm audit (frontend).
#
# Usage : ./scripts/audit_deps.sh           # advisory (exit 0 même si vulns signalées)
#         ./scripts/audit_deps.sh --strict  # CI gating : exit 1 si vuln non ignorée
#
# Politique (SECURITY.md) :
#   - CI exécute --strict. Une vuln HIGH/CRITICAL non listée dans .audit-ignore bloque la PR.
#   - .audit-ignore liste les CVE acceptées (une par ligne, # = commentaire) avec justification
#     dans SECURITY.md + date de réévaluation. pip-audit les ignore via --ignore-vuln.
#   - npm audit nécessite package-lock.json — skip gracieux si absent (frontend statique).
set -euo pipefail
cd "$(dirname "$0")/.."
STRICT="${1:-}"

# Construction des --ignore-vuln depuis .audit-ignore (CVEs acceptées + justifiées).
IGNORE_ARGS=()
if [ -f .audit-ignore ]; then
  while IFS= read -r line; do
    cve="${line%%#*}"          # strip commentaires
    cve="$(echo "$cve" | tr -d '[:space:]')"
    if [ -n "$cve" ]; then
      IGNORE_ARGS+=(--ignore-vuln "$cve")
    fi
  done < .audit-ignore
fi

echo "=== pip-audit (backend/requirements.txt) ==="
if command -v pip-audit >/dev/null 2>&1; then
  if [ "$STRICT" = "--strict" ]; then
    if [ "${#IGNORE_ARGS[@]}" -gt 0 ]; then
      pip-audit -r backend/requirements.txt "${IGNORE_ARGS[@]}"
    else
      pip-audit -r backend/requirements.txt
    fi
  else
    if [ "${#IGNORE_ARGS[@]}" -gt 0 ]; then
      pip-audit -r backend/requirements.txt "${IGNORE_ARGS[@]}" || echo "  (vulnérabilités signalées — mode advisory, exit 0)"
    else
      pip-audit -r backend/requirements.txt || echo "  (vulnérabilités signalées — mode advisory, exit 0)"
    fi
  fi
else
  echo "  pip-audit absent — installer : pip install pip-audit"
  [ "$STRICT" = "--strict" ] && exit 3  # strict sans pip-audit = échec CI (outil requis)
fi

echo ""; echo "=== npm audit (frontend) ==="
if [ ! -f frontend/package-lock.json ]; then
  echo "  skip : pas de package-lock.json (frontend statique sans deps npm runtime)."
elif command -v npm >/dev/null 2>&1; then
  if [ "$STRICT" = "--strict" ]; then
    ( cd frontend && npm audit --audit-level=high )
  else
    ( cd frontend && npm audit --audit-level=high ) || echo "  (vulnérabilités signalées — mode advisory, exit 0)"
  fi
else
  echo "  npm absent — skip"
fi
echo "✓ Audit deps terminé ($([ "$STRICT" = "--strict" ] && echo strict || echo advisory))."
