"""Envoi d'e-mails (SMTP réel). Mode dry-run si smtp_enabled=false (log au lieu d'envoi).

Utilise smtplib (stdlib) — pas de dépendance supplémentaire. L'envoi est synchrone
(les endpoints appelants tournent en threadpool FastAPI). En cas d'échec SMTP,
l'appelant reçoit email_sent=False (le log Notification garde l'échec pour relance).

P10 : send_invitation_email (membres). P13 : send_email générique (prospection CRM) +
refactor du SMTP dans _smtp_send (partagé). Signature bool de send_invitation_email
préservée (pas de régression members.py).
"""
import logging
import smtplib
from email.message import EmailMessage

from .config import settings

log = logging.getLogger("revpulse.mailer")


def _smtp_send(msg: EmailMessage) -> tuple[bool, str]:
    """Envoie un EmailMessage via SMTP (TLS + auth selon config). Retourne (success, detail).
    Dry-run si smtp_enabled=false (log, considéré OK en dev/test — aucun SMTP configuré)."""
    if not settings.smtp_enabled:
        log.info("[mailer dry-run] %s → %s", msg["Subject"], msg["To"])
        return True, "dry-run (smtp_enabled=false)"
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
            if settings.smtp_use_tls:
                s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password or "")
            s.send_message(msg)
        log.info("[mailer] envoyé → %s", msg["To"])
        return True, "sent"
    except Exception as e:  # SMTP peut échouer pour plein de raisons ; on ne casse pas l'appelant
        log.error("[mailer] échec envoi → %s : %s", msg["To"], e)
        return False, f"{type(e).__name__}: {str(e)[:150]}"


def send_email(to_email: str, subject: str, body_text: str,
               body_html: str | None = None, from_name: str | None = None) -> tuple[bool, str]:
    """Envoie un e-mail générique (P13 — prospection CRM). Retourne (success, detail).
    From = settings.smtp_from (ou 'Name <from>' si from_name). HTML optionnel (multipart).
    Dry-run si SMTP désactivé. L'appelant crée la ligne Notification selon le résultat."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{settings.smtp_from}>" if from_name else settings.smtp_from
    msg["To"] = to_email
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    return _smtp_send(msg)


def _build_invitation(to_email: str, invite_link: str, tenant_name: str, role: str, inviter_name: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"Invitation à rejoindre « {tenant_name} » sur RevPulse"
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg.set_content(f"""Bonjour,

{inviter_name} vous invite à rejoindre l'espace « {tenant_name} » sur RevPulse
en tant que {role}.

Acceptez l'invitation en cliquant sur ce lien (valide 7 jours) :
{invite_link}

— L'équipe RevPulse""")
    return msg


def send_invitation_email(to_email: str, invite_link: str, tenant_name: str,
                          role: str, inviter_name: str) -> bool:
    """Envoie l'e-mail d'invitation (P10, signature bool préservée). Délègue à _smtp_send."""
    msg = _build_invitation(to_email, invite_link, tenant_name, role, inviter_name)
    ok, _ = _smtp_send(msg)
    return ok
