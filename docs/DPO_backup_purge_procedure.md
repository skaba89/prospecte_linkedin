# P69 — Procédure DPO : purge des sauvegardes post-effacement GDPR (Art. 17.3)

## Contexte

Quand un utilisateur exécute son droit à l'effacement (`DELETE /api/tenants/{tid}/gdpr/erase?user_id=X`),
la cascade P65 (G3) scrubbée les données dans la **DB live** :
- `User.email` / `User.name` → anonymisés (`Anonymized (GDPR)` + `erased_{id}@revpulse.io`).
- `Member.email` / `Member.name` → anonymisés.
- `Contact` (owner_id) → PII (email, phone, name) → null/anonymisé.
- `AuditLog.actor_name` → `Anonymized (GDPR)` ; `AuditLog.detail` (JSON) → email original remplacé.
- `UserEmailIntegration` (OAuth Gmail/Outlook) → hard-deleted (tokens révoqués).
- `CV.raw_text` / `CV.parsed_data` → vidés.

**Problème** : les **sauvegardes** (backups PostgreSQL quotidiens via `backup_cron.sh`) antérieures à
l'effacement contiennent encore les PII. Une **restauration de backup** réintroduit ces PII dans la DB
restaurée → le user « effacé » réapparaît avec ses données originales → **Art. 17 RGPD violé**.

## Procédure obligatoire après TOUTE restauration de backup

### 1. Restaurer le backup (procédure ops standard)

```bash
# Restaurer le dernier backup PostgreSQL
PGPASSWORD=$DB_PASSWORD pg_restore -h $DB_HOST -U $DB_USER -d $DB_NAME -c /backups/revpulse_YYYYMMDD.dump
```

### 2. Exécuter le script de re-purge P69 G4

```bash
REVPULSE_DATABASE_URL=postgresql://$DB_USER:$DB_PASSWORD@$DB_HOST/$DB_NAME \
  python scripts/replay_gdpr_erase_post_restore.py

# Mode dry-run (audit sans modifier) :
REVPULSE_DATABASE_URL=... python scripts/replay_gdpr_erase_post_restore.py --dry-run
```

Le script :
1. Scanne tous les `users` avec `gdpr_erased_at IS NOT NULL` (= effacés avant la restauration).
2. Pour chacun : **re-applique la cascade d'effacement** (re-scrub User/Member/Contacts/AuditLog.detail/
   OAuth tokens/CVs qui ont été réintroduits par la restauration).
3. Logge une entrée `audit_logs` avec `action='gdpr.erase.replay'` (preuve DPO de la re-purge
   post-restauration, avec timestamp + actor `DPO replay (P69 G4)`).

### 3. Vérifier

```sql
-- Aucun user effacé ne doit avoir ses PII originales restaurées :
SELECT id, email, name, gdpr_erased_at FROM users WHERE gdpr_erased_at IS NOT NULL;
-- Toutes les entrées audit gdpr.erase.replay :
SELECT created_at, actor_name, target_id, detail FROM audit_logs WHERE action='gdpr.erase.replay';
```

## Rétention des backups (Art. 17.3.b/e)

Les backups sont conservés **30 jours** (cf. `backup_cron.sh` — rotation `find /backups -mtime +30 -delete`).
Au-delà de 30 jours, les backups contenant les PII d'un user effacé sont **automatiquement purgés** par
la rotation. La procédure ci-dessus couvre la fenêtre 0-30 jours (restauration possible).

## Exceptions de conservation (Art. 17.3.b/e)

Les enregistrements **structurels** (FK integrity, `audit_logs` immuables SOC2 7 ans) sont conservés :
- `users.id` (FK) préservé mais données anonymisées.
- `audit_logs` : `actor_user_id` conservé (traçabilité), `actor_name` + `detail` scrubbés.
- `members.id` (FK) préservé mais données anonymisées.

## Validation

Le test `tests/test_p69_gdpr_g4_purge.py` valide :
- Le script `replay_gdpr_erase_post_restore.py` existe + est exécutable.
- Le script référence `gdpr_erased_at` + `gdpr.erase.replay` (audit log).
- `PATCH /auth/me` (rectification Art. 16) — name + locale modifiables, email non modifiable.
- `GET /gdpr/export` logge `audit_logs.action='gdpr.export'` (preuve DPO Art. 15/20).
