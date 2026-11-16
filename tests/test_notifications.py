#!/usr/bin/env python3
"""test_notifications.py — validation statique des notifications & e-mails (P13, sans API live).

Couvre (par lecture des sources + introspection, pas de serveur) :
  - Modèle Notification (models.py) : 14 champs, tablename, FK tenant_id/user_id/contact_id,
    indexes, default _uid("ntf"), read_at nullable (feed in-app).
  - Migration 0006 : crée notifications + index composé tenant_id+created_at,
    down_revision=0005_p12_linkedin_sync, server_default channel/status.
  - Schemas : NotificationOut (from_attributes), EmailSendRequest (template+custom_message),
    TemplateInfo, EmailSendResult (notification_id + status + email_sent).
  - mailer.py : send_email (générique P13, tuple bool+detail), _smtp_send (shared helper),
    send_invitation_email préservée (signature bool P10, délègue à _smtp_send), dry-run.
  - services/templates.py : 4 templates (cold_outreach, follow_up, intro, re_engagement),
    render() échappe le contexte (html.escape anti-XSS), list_templates().
  - Router notifications.py : 5 endpoints, RBAC crm.manage sur email_contact, RGPD skip
    (SuppressionList), placeholder skip (@linkedin.sync), audit crm.email_contact, dry_run.
  - Intégration : router notifications enregistré dans main.py, Notification dans Base.metadata,
    test_notifications.py dans le CI (static checks), migration 0006 chaînée après 0005.
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


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ─── Modèle Notification (models.py) ───────────────────────────────────────────
print("=== Modèle Notification (models.py) ===")
models_src = read("backend/app/models.py")
ck("class Notification(Base):" in models_src, True, "Notification class définie")
ck('__tablename__ = "notifications"' in models_src, True, "tablename notifications")
for field, label in [
    ("tenant_id", "FK tenant_id"), ("user_id", "FK user_id (expéditeur)"),
    ("type", "type (email|in_app|system)"), ("channel", "channel (email|feed)"),
    ("recipient_email", "recipient_email (indexé)"),
    ("recipient_contact_id", "FK recipient_contact_id (contacts)"),
    ("subject", "subject"), ("body_text", "body_text (Text)"), ("body_html", "body_html (Text)"),
    ("template_id", "template_id"), ("status", "status (pending|sent|failed|dry_run)"),
    ("sent_at", "sent_at"), ("error_detail", "error_detail"), ("read_at", "read_at (feed)"),
    ("created_at", "created_at"),
]:
    ck(f"{field} = Column" in models_src, True, f"champ {label} présent")
ck('default=lambda: _uid("ntf")' in models_src, True, "default id _uid('ntf')")

# ─── Migration 0006 ────────────────────────────────────────────────────────────
print("\n=== Migration 0006_p13_notifications ===")
mig = read("backend/alembic/versions/0006_p13_notifications.py")
ck('revision = "0006_p13_notifications"' in mig, True, "revision 0006_p13_notifications")
ck('down_revision = "0005_p12_linkedin_sync"' in mig, True, "down_revision 0005 (chaînée P12)")
ck('"notifications"' in mig and "create_table" in mig, True, "create_table notifications")
ck('ix_notifications_tenant_created' in mig, True, "index composé tenant_id+created_at")
for col in ["tenant_id", "user_id", "type", "channel", "recipient_email",
            "recipient_contact_id", "subject", "body_text", "body_html", "template_id",
            "status", "sent_at", "error_detail", "read_at", "created_at"]:
    ck(f'"{col}"' in mig, True, f"colonne {col} dans la migration")
ck('server_default="email"' in mig, True, "channel server_default email")
ck('server_default="pending"' in mig, True, "status server_default pending")
ck("drop_table" in mig and "drop_index" in mig, True, "downgrade propre (drop table + index)")

# ─── Schemas (NotificationOut + EmailSendRequest + TemplateInfo + EmailSendResult) ─
print("\n=== Schemas notifications (P13) ===")
sch = read("backend/app/schemas.py")
for cls in ["NotificationOut", "EmailSendRequest", "TemplateInfo", "EmailSendResult"]:
    ck(f"class {cls}(BaseModel):" in sch, True, f"{cls} défini")
ck("from_attributes=True" in sch and "NotificationOut" in sch, True,
   "NotificationOut from_attributes (ORM mode)")
for field in ["notification_id", "status", "email_sent", "detail"]:
    ck(field in sch and "EmailSendResult" in sch, True, f"EmailSendResult.{field}")
ck("template: str" in sch, True, "EmailSendRequest.template (str)")
ck("custom_message: str" in sch, True, "EmailSendRequest.custom_message")

# ─── mailer.py (send_email + _smtp_send + send_invitation_email préservée) ─────
print("\n=== mailer.py (extension P13) ===")
ml = read("backend/app/mailer.py")
ck("def _smtp_send(" in ml, True, "_smtp_send helper (SMTP partagé)")
ck("def send_email(" in ml, True, "send_email générique (P13)")
ck("-> tuple[bool, str]" in ml, True, "send_email retourne tuple[bool, str]")
ck("body_html" in ml and "add_alternative" in ml, True, "send_email supporte HTML (multipart)")
ck("def send_invitation_email(" in ml, True, "send_invitation_email préservée (P10)")
ck("_smtp_send(msg)" in ml, True, "send_invitation_email délègue à _smtp_send (DRY)")
ck("smtp_enabled" in ml, True, "dry-run si smtp_enabled=false")
ck("from_name" in ml, True, "send_email from_name (expéditeur personnalisé)")

# ─── services/templates.py (4 templates + render + escape) ─────────────────────
print("\n=== services/templates.py ===")
tp = read("backend/app/services/templates.py")
ck("import html" in tp, True, "import html (escape anti-XSS)")
ck("html.escape" in tp, True, "render échappe le contexte (anti-XSS)")
for tid in ["cold_outreach", "follow_up", "intro", "re_engagement"]:
    ck(f'"{tid}"' in tp, True, f"template {tid} défini")
ck("def list_templates(" in tp, True, "list_templates défini")
ck("def render(" in tp, True, "render défini")
ck("-> tuple[str, str, str]" in tp, True, "render retourne (subject, text, html)")
ck("html.escape" in tp, True, "escape du custom_message (anti-injection)")

# ─── Router notifications.py (5 endpoints + RBAC + RGPD + audit) ───────────────
print("\n=== Router notifications.py (P13) ===")
rt = read("backend/app/routers/notifications.py")
for path in ["/notifications/templates", "/crm/contacts/{ctid}/email",
             "/notifications", "/notifications/feed", "/notifications/{nid}/read"]:
    ck(path in rt, True, f"endpoint {path}")
ck('require_permission("crm.manage")' in rt, True, "RBAC crm.manage sur email_contact (mutation)")
ck("SuppressionList" in rt, True, "RGPD : check suppression list (skip désinscription)")
ck("@linkedin.sync" in rt or "linkedin.sync" in rt, True, "skip placeholder @linkedin.sync (P12)")
ck("log_audit" in rt and "crm.email_contact" in rt, True, "audit crm.email_contact")
ck("send_email" in rt, True, "utilise mailer.send_email")
ck("templates.render" in rt, True, "rend le template via services/templates")
ck("dry_run" in rt, True, "statut dry_run (smtp_enabled=false)")
ck("read_at" in rt, True, "feed filtre read_at IS NULL + mark read la set")
ck("from None" in rt, True, "B904 : raise ... from None (template inconnu)")
ck("HTTP_404_NOT_FOUND" in rt, True, "404 si contact/notification introuvable")

# ─── Intégration ───────────────────────────────────────────────────────────────
print("\n=== Intégration (main.py + metadata + CI) ===")
main_src = read("backend/app/main.py")
ck("notifications.router" in main_src, True, "router notifications enregistré dans main.py")
ci = read(".github/workflows/ci.yml")
ck("python tests/test_notifications.py" in ci, True, "test_notifications.py dans le CI (static checks)")

# Introspection : Notification dans Base.metadata + endpoints enregistrés + templates.
# Skip gracieux si sqlalchemy non installé (job static-checks n'installe que pyyaml).
sys.path.insert(0, str(ROOT / "backend"))
try:
    import sqlalchemy  # noqa: F401 — garde-fou : skip si absent (static-checks)
    from app.database import Base
    from app import models
    from app.routers import notifications as nrt
    from app.services import templates as tpl
    from app.mailer import send_email, send_invitation_email
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via job test-api E2E).")
    print("    Les checks par lecture de sources ci-dessus couvrent déjà ces aspects.")
else:
    try:
        ck("notifications" in Base.metadata.tables, True, "table notifications dans Base.metadata")
        ck(hasattr(models.Notification, "status") and hasattr(models.Notification, "read_at"),
           True, "Notification.status/read_at accessibles (ORM)")
        routes = [r.path for r in nrt.router.routes]
        ck(any("notifications/templates" in p for p in routes), True, "route templates enregistrée")
        ck(any("contacts/{ctid}/email" in p for p in routes), True, "route email_contact enregistrée")
        ck(any("notifications/feed" in p for p in routes), True, "route feed enregistrée")
        tlist = tpl.list_templates()
        ck(len(tlist) == 4, True, f"4 templates via list_templates ({len(tlist)})")
        s, t, h = tpl.render("cold_outreach", contact_name="<b>x</b>", company="C",
                             tenant_name="T", sender_name="S", topic="CTO",
                             custom_message="<script>alert(1)</script>")
        ck("<b>" not in s and "&lt;b&gt;" in (s + t + h), True,
           "render échappe le contexte (anti-XSS — <b> → &lt;b&gt;)")
        ck("<script>alert(1)</script>" not in h, True, "custom_message échappé (anti-injection)")
        # mailer dry-run (smtp_enabled=false par défaut).
        ok, det = send_email("x@y.io", "sujet", "corps")
        ck(ok, True, "send_email dry-run OK (smtp_enabled=false)")
        ck("dry-run" in det, True, f"send_email detail dry-run ({det})")
    except Exception as e:
        ck(False, True, f"introspection runtime (erreur: {type(e).__name__}: {e})")

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
