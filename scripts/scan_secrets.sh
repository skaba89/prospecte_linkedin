#!/usr/bin/env bash
# Scanner de secrets en code committé (grep, patterns de PAT/clés réelles + webhook).
#
# Détecte : GitHub PAT (ghp_/github_pat_), GitLab PAT (glpat-), AWS access key (AKIA),
#           private keys, Stripe (sk_live_), Slack (xox* + webhook hooks.slack.com),
#           Google API key (AIza).
# Allowlist : .env.example, docker-compose*.yml, tests/, README.md, SECURITY.md,
#             scripts/test_backup_restore.sh, backups/, *.sql.gz, .git/, node_modules/.
# Exit 1 si un secret est trouvé, 0 si le code est propre.
#
# CI : exécuté via tests/test_security.py (qui asserte exit 0).
set -euo pipefail
cd "$(dirname "$0")/.."

PATTERNS='ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82}|glpat-[A-Za-z0-9_-]{20}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk_live_[A-Za-z0-9]{24}|xox[baprs]-[A-Za-z0-9-]+|hooks\.slack\.com/services/[A-Za-z0-9/_-]+|AIza[0-9A-Za-z_-]{35}'

HITS=$(find . -type f \
    -not -path './.git/*' \
    -not -path './node_modules/*' \
    -not -path '*/node_modules/*' \
    -not -path './backups/*' \
    -not -path './backups-test/*' \
    -not -name '*.sql.gz' \
    -not -name '*.sql.gz.gpg' \
    -not -name '.env.example' \
    -not -name 'docker-compose*.yml' \
    -not -name 'docker-compose*.yaml' \
    -not -path './tests/*' \
    -not -name 'test_backup_restore.sh' \
    -not -name 'README.md' \
    -not -name 'SECURITY.md' \
    -not -name 'scan_secrets.sh' \
    -not -name '.audit-ignore' \
    -print0 2>/dev/null \
  | xargs -0 grep -EnI "$PATTERNS" 2>/dev/null || true)

if [ -n "$HITS" ]; then
  echo "✗ Secrets détectés dans le code committé :" >&2
  echo "$HITS" >&2
  exit 1
fi
echo "✓ Aucun secret détecté (patterns : ghp_/github_pat_/glpat-/AKIA/PRIVATE KEY/sk_live_/xox/Slack webhook/AIza)."
