"""Envoi d'e-mails (SMTP réel). Mode dry-run si smtp_enabled=false (log au lieu d'envoi).

Utilise smtplib (stdlib) — pas de dépendance supplémentaire. L'envoi est synchrone
(les endpoints appelants tournent en threadpool FastAPI). En cas d'échec SMTP,
l'invitation reste créée côté DB ; l'appelant reçoit email_sent=False pour
décider d'une relance.
"""
import logging
import smtplib
from email.message import EmailMessage

from .config import settings

log = logging.getLogger("revpulse.mailer")


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
    """Envoie l'e-mail d'invitation. Retourne True si envoyé (ou dry-run), False si échec SMTP."""
    msg = _build_invitation(to_email, invite_link, tenant_name, role, inviter_name)
    if not settings.smtp_enabled:
        log.info("[mailer dry-run] invitation → %s | lien=%s", to_email, invite_link)
        return True  # dry-run : considéré OK en dev/test (aucun SMTP configuré)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
            if settings.smtp_use_tls:
                s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password or "")
            s.send_message(msg)
        log.info("[mailer] invitation envoyée → %s", to_email)
        return True
    except Exception as e:  # SMTP peut échouer pour plein de raisons ; on ne casse pas l'invite
        log.error("[mailer] échec envoi invitation → %s : %s", to_email, e)
        return False
