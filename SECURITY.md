# Sécurité RevPulse — politique et pratiques (P10)

## Signaler une vulnérabilité
Signaler en privé à `security@datasphere-gn.example` (**ne pas** ouvrir d'issue publique
pour une vulnérabilité de sécurité). Délai de réponse visé : 72h. Programme de coordination
à définir sous DataSphere Innovation Guinée.

## Scanner de secrets en code committé (P10.12)
- `scripts/scan_secrets.sh` grep 9 patterns : GitHub PAT (`ghp_`/`github_pat_`), GitLab PAT
  (`glpat-`), AWS access key (`AKIA`), private keys (`-----BEGIN … PRIVATE KEY-----`),
  Stripe (`sk_live_`), Slack (`xox*` + webhook `hooks.slack.com`), Google API key (`AIza`).
- Allowlist : `.env.example`, `docker-compose*.yml`, `tests/`, `README.md`, `SECURITY.md`,
  `.audit-ignore`, `backups/`, `backups-test/`, `*.sql.gz`, `.git/`, `node_modules/`.
- CI : exécuté via `tests/test_security.py` (assert exit 0). Un secret détecté **bloque la PR**.

## Audit de dépendances — bloquant en CI (P10.12)
- `scripts/audit_deps.sh` : `pip-audit` (backend/requirements.txt) + `npm audit` (frontend,
  skip gracieux si pas de `package-lock.json` — frontend statique sans deps npm runtime).
- **CI exécute `make audit-deps -- --strict`** → exit 1 si vuln HIGH/CRITICAL non ignorée.
- `.audit-ignore` : CVE acceptées (une par ligne, `#` = commentaire). pip-audit les ignore via
  `--ignore-vuln`. **Toute CVE listée doit être justifiée + datée ci-dessous** avec échéance de
  réévaluation, et validée par le responsable sécurité.
- Pour accepter un risque : (1) bumper la dépendance si un correctif existe ; (2) sinon, ajouter
  la CVE à `.audit-ignore` + documenter la justification + l'échéance dans cette section.

### CVE acceptées (justifications + échéances)
python-jose bumpé 3.3.0 → **3.4.0** (CVE alg-confusion PYSEC-2024-232/233 + PYSEC-2025-185 fixées).
PYSEC-2026-4298 persiste en 3.4.0 (jose non maintenu, pas de fix amont) — acceptée (usage HS256 +
`algorithms=` explicite). Les autres CVE restantes sont **transitives** (sans contrôle direct).

**P24-A** : tentative d'upgrade pyasn1 0.4.8 → 0.6.5 — **bloquée** par la contrainte
`python-jose 3.4.0 requires pyasn1<0.5.0` (jose pin dur). Le fix réel = migration `python-jose →
PyJWT` (PyJWT n'a pas ces deps transitives pyasn1/ecdsa). Re-planifiée en **P24-B** (AI Gateway —
les providers IA utilisent des auths JWT-like, naturel pour basculer la stack JWT en même temps).
ecdsa 0.19.2 déjà à la latest (pas de fix amont pour PYSEC-2026-1325). starlette 0.50 nécessite un
bump FastAPI coordonné (P25+).

| CVE | Paquet | Sévérité | Justification | Échéance rééval. |
|-----|--------|----------|---------------|------------------|
| PYSEC-2026-4298 | python-jose 3.4.0 | variable | jose non maintenu, pas de fix amont ; RevPulse utilise HS256 + `algorithms=` explicite (mitigue alg-confusion) ; migration PyJWT planifiée | P24-B |
| PYSEC-2026-2263, 3455/3456/3457, CVE-2026-119194/5/6/7 | pyasn1 0.4.8 | variable | Transitive (jose→ecdsa→pyasn1) ; P24-A a tenté upgrade 0.6.5 → bloqué par jose (requires pyasn1<0.5.0) ; fix = migration jose→PyJWT | P24-B |
| PYSEC-2026-161, 2280/2281, 248/249 | starlette 0.50 | HIGH (DoS) | Transitive (FastAPI) ; mitigé par reverse proxy nginx (limit headers) + rate-limit ; bump FastAPI/starlette coordonné planifié | P25+ |
| PYSEC-2026-1325 | ecdsa 0.19.2 | variable | Transitive (jose) ; déjà à la latest 0.19.2 (pas de fix amont) ; RevPulse utilise HS256 (algos EC de jose non utilisés) | P24-B |

## Chiffrement des tokens OAuth (P10.2)
- `access_token` LinkedIn stocké **chiffré** (Fernet, `cryptography`). Jamais renvoyé par l'API.
- Clé : `REVPULSE_TOKEN_ENCRYPTION_KEY` (Fernet base64-urlsafe 32 bytes). **Requise en production**
  (fail-fast dans `config.Settings._fail_fast_secrets`).
- Rotation : `REVPULSE_TOKEN_ENCRYPTION_KEY_PREVIOUS` (ancienne clé, décryptage only). Procédure :
  générer une nouvelle clé → la mettre dans `_KEY` + l'ancienne dans `_PREVIOUS` → redémarrer →
  les tokens sont déchiffrés avec l'ancienne + rechiffrés avec la nouvelle au prochain stockage.
- Migration 0003 chiffre les tokens existants (idempotent : préfixe Fernet `gAAAA` détecté, no-op
  si la clé est absente — fresh dev/test DB).

## State OAuth — anti-CSRF + anti-rejeu (P10.3)
- State = JWT signé `{tid, nonce, exp court (10 min)}` + nonce à usage unique (table `oauth_states`).
- Le callback valide la signature + le nonce (existant, **non utilisé**, non expiré) → marque
  `used=True`. Un même state **ne peut pas être rejoué**.

## Sessions + révocation (P10.7)
- JWT `{sub, exp, jti}`. `POST /api/auth/logout` révoque le jti courant (table `revoked_tokens`).
- `get_current_user` vérifie la révocation à chaque requête → **invalidation immédiate**.
- Nettoyage : les rows `revoked_tokens` où `expires_at < now` sont purgables (cron recommandé).
- Invitations : token `secrets`-random, expiry 7j, single-use (mis à null à l'acceptation).

## Compte démo + mock — interdits en production (P10.4)
- `/api/auth/demo`, le seed démo (`seed_all`/`ensure_demo`), le mock LinkedIn (`mock-connect`,
  publish mock) sont **interdits en production** (gate `settings.is_production`). Dev/CI seulement.
- Le hint d'identifiants démo sur 401 login est retiré en production.

## Membre inactif (P10.8)
- `require_permission` rejette tout membre `status != "active"` (invitation en attente / expirée
  / suspendue) → 403. Aucune permission accordée à un membre inactif.

## Publications — dédup + statut durable + séparation par capacité (P10.9/10)
- Dédup par `content_hash` (sha256 du texte normalisé) : un même contenu déjà publié → 409.
- Statut durable (table `publications`) : `published` | `failed` | `pending` | `manual_required`.
- **post** : automatisable (ugcPosts API, scope `w_member_social`).
- **comment** : automatisable (socialActions API, requiert `parent_urn` du post parent).
- **dm** : **NON automatisable** (API LinkedIn messaging restreinte) → statut `manual_required`
  (tâche manuelle assistée). RevPulse ne contourne jamais les restrictions LinkedIn.

## Sauvegardes — sécurité + isolation (P10.16/17)
- `backup_db.sh` : `chmod 600` (permissions) + `sha256` (intégrité) + chiffrement GPG optionnel
  (`BACKUP_ENCRYPTION_PASSPHRASE`) + stockage externe optionnel (`BACKUP_REMOTE_CMD`).
- Tests isolés dans `./backups-test/` (**jamais** mélangés aux backups réels `./backups/`).
- Round-trip restore vérifié : `make backup-test` (postgres éphémère docker).

## Headers HTTP + ports (P6 + P10.11)
- nginx : X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy,
  Permissions-Policy, Content-Security-Policy, Strict-Transport-Security.
- Ports : API (8000), Grafana (3000), Prometheus (9090) liés à `127.0.0.1` (admin-only).
  PostgreSQL : interne (pas de port host). Frontend (8080) : point d'entrée public.

## Identifiants par défaut (P10.5)
- `POSTGRES_PASSWORD`, `REVPULSE_JWT_SECRET`, `REVPULSE_TOKEN_ENCRYPTION_KEY`,
  `GRAFANA_ADMIN_PASSWORD` : **aucun défaut** en production (fail-fast `${VAR:?…}` dans compose).
- En dev/CI : valeurs tolérées (env=test, jwt secret démo, pas de clé Fernet → tokens plaintext
  en dev seulement).
