#!/usr/bin/env bash
# Scanner de secrets en code committé (grep, patterns de PAT/clés réelles).
#
# Détecte : GitHub PAT (ghp_/github_pat_), AWS access key (AKIA), private keys,
#           Stripe (sk_live_), Slack (xox*), Google API key (AIza).
# Allowlist : .env.example, docker-compose*.yml, tests/, README.md,
#             scripts/test_backup_restore.sh, backups/, *.sql.gz, .git/, node_modules/.
# Exit 1 si un secret est trouvé, 0 si le code est propre.
#
# CI : exécuté via tests/test_security.py (qui asserte exit 0).
set -euo pipefail
cd "$(dirname "$0")/.."

PATTERNS='ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk_live_[A-Za-z0-9]{24}|xox[baprs]-[A-Za-z0-9-]+|AIza[0-9A-Za-z_-]{35}'

HITS=$(find . -type f \
    -not -path './.git/*' \
    -not -path './node_modules/*' \
    -not -path '*/node_modules/*' \
    -not -path './backups/*' \
    -not -name '*.sql.gz' \
    -not -name '.env.example' \
    -not -name 'docker-compose*.yml' \
    -not -name 'docker-compose*.yaml' \
    -not -path './tests/*' \
    -not -name 'test_backup_restore.sh' \
    -not -name 'README.md' \
    -not -name 'scan_secrets.sh' \
    -print0 2>/dev/null \
  | xargs -0 grep -EnI "$PATTERNS" 2>/dev/null || true)

if [ -n "$HITS" ]; then
  echo "✗ Secrets détectés dans le code committé :" >&2
  echo "$HITS" >&2
  exit 1
fi
echo "✓ Aucun secret détecté dans le code committé (patterns : ghp_ / github_pat_ / AKIA / PRIVATE KEY / sk_live_ / xox / AIza)."
