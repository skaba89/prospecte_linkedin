"""Modèles SQLAlchemy multi-tenant.

Toute table « métier » (Profile, Member, Approval, DashboardModule) est scopée
par `tenant_id` → isolation par espace de travail. Les membres portent aussi un
`user_id` (nullable : null pour une invitation en attente non encore rattachée).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

from .database import Base


def _json_default(o):
    """Encoder JSON fallback : sérialise datetime/date → isoformat (audit detail, segment criteria,
    etc.). Évite le TypeError « Object of type datetime is not JSON serializable » quand un router
    loggue un dict contenant des timestamps (ex: P23 task.update avec completed_at)."""
    from datetime import date as _date
    from datetime import datetime as _dt
    if isinstance(o, (_dt, _date)):
        return o.isoformat()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


class JSONType(TypeDecorator):
    """JSON portable : JSONB sous Postgres, JSON sinon (SQLite)."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        import json

        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, default=_json_default)

    def process_result_value(self, value, dialect):
        import json

        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        return json.loads(value)


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: _uid("usr"))
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(String, primary_key=True, default=lambda: _uid("tnt"))
    name = Column(String, nullable=False)
    plan = Column(String, default="Starter", nullable=False)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Profile(Base):
    """Profil connecté à l'espace (LinkedIn simulé ou compte RevPulse natif). 1 par tenant."""

    __tablename__ = "profiles"
    id = Column(String, primary_key=True, default=lambda: _uid("prf"))
    tenant_id = Column(String, ForeignKey("tenants.id"), unique=True, nullable=False)
    type = Column(String, default="native", nullable=False)  # linkedin | native
    name = Column(String, default="")
    headline = Column(String, default="")
    company = Column(String, default="")
    followers = Column(Integer, default=0)
    initials = Column(String, default="")


class Member(Base):
    """Ligne d'appartenance tenant x utilisateur (ou invitation en attente si user_id est null)."""

    __tablename__ = "members"
    id = Column(String, primary_key=True, default=lambda: _uid("mem"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    email = Column(String, nullable=False)
    name = Column(String, nullable=False)
    role = Column(String, default="Viewer", nullable=False)
    status = Column(String, default="invited", nullable=False)  # active | invited
    token = Column(String, nullable=True)
    invited_by = Column(String, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)  # invitation : date d'expiration (null = sans limite)


class Approval(Base):
    """Contenu en file d'approbation, scopé par tenant."""

    __tablename__ = "approvals"
    id = Column(String, primary_key=True, default=lambda: _uid("apr"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    type = Column(String, default="")
    title = Column(String, default="")
    submitter = Column(String, default="")
    submitted_ago = Column(String, default="")
    priority = Column(String, default="medium")
    status = Column(String, default="pending")
    risk_flags = Column(String, default="Aucun")
    summary = Column(Text, default="")
    content_body = Column(Text, default="")  # texte complet du post/DM (pour publication LinkedIn)
    type_label = Column(String, default="")
    type_color = Column(String, default="#635bff")
    priority_label = Column(String, default="Normal")
    priority_color = Column(String, default="#635bff")
    status_label = Column(String, default="En attente")
    status_color = Column(String, default="#f59e0b")
    approved = Column(Boolean, default=False, nullable=False)


class DashboardModule(Base):
    """Un module de dashboard (clé → payload JSON), scopé par tenant."""

    __tablename__ = "dashboard_modules"
    id = Column(String, primary_key=True, default=lambda: _uid("mod"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    module_key = Column(String, nullable=False)
    payload = Column(JSONType, nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "module_key", name="uq_tenant_module"),)


class LinkedInConnection(Base):
    """Connexion LinkedIn OAuth d'un tenant (1 par tenant).

    P10.2 + P32 : access_token stocké CHIFFRÉ (Fernet, rotation via
    REVPULSE_TOKEN_ENCRYPTION_KEY_PREVIOUS) — voir services/security.encrypt_token/decrypt_token +
    routers/linkedin._store_token/_load_token. Le nom de colonne access_token est historique (P10.2
    chiffrait en place faute de colonne _enc dédiée) ; la valeur stockée est le ciphertext Fernet
    (préfixe gAAAA...) quand une clé est configurée, sinon plaintext legacy dev/test. Ne JAMAIS
    exposer dans une réponse API (status renvoie seulement member_name/id).
    """

    __tablename__ = "linkedin_connections"
    id = Column(String, primary_key=True, default=lambda: _uid("lk"))
    tenant_id = Column(String, ForeignKey("tenants.id"), unique=True, nullable=False)
    access_token = Column(Text, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    member_id = Column(String, default="")
    member_name = Column(String, default="")
    member_headline = Column(String, default="")
    scope = Column(String, default="")
    connected_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """Trace immuable des actions mutantes d'un tenant (qui a fait quoi, quand).

    Scopée par tenant_id (String indexé SANS FK → survit à la suppression du
    tenant, une audit log devant outliver l'entité auditée). Alimentée par
    audit.log_audit() dans les routers mutantes. Le request_id corrélationne
    avec les logs structurés P3.
    """

    __tablename__ = "audit_logs"
    id = Column(String, primary_key=True, default=lambda: _uid("aud"))
    tenant_id = Column(String, nullable=False, index=True)  # pas de FK (survie)
    actor_user_id = Column(String, nullable=True)  # null pour actions système (OAuth callback)
    actor_name = Column(String, default="", nullable=False)
    action = Column(String, nullable=False)  # ex: "approval.approve", "member.invite"
    target_type = Column(String, default="", nullable=False)
    target_id = Column(String, default="", nullable=False)
    detail = Column(JSONType, nullable=True)
    request_id = Column(String, default="", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class OAuthState(Base):
    """State OAuth à usage unique (anti-CSRF + anti-rejeu). nonce aléatoire stocké,
    marqué 'used' au callback → un même state ne peut être rejoué (P10.3)."""

    __tablename__ = "oauth_states"
    id = Column(String, primary_key=True, default=lambda: _uid("ost"))
    tenant_id = Column(String, nullable=False, index=True)
    nonce = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class RevokedToken(Base):
    """JTI révoqué (logout / révocation manuelle). La vérification se fait dans
    get_current_user (query par jti) — decode_token reste pur. Nettoyage : rows où
    expires_at < now (P10.7)."""

    __tablename__ = "revoked_tokens"
    jti = Column(String, primary_key=True)
    user_id = Column(String, nullable=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, default=datetime.utcnow)


class Publication(Base):
    """Trace durable + dédup des publications LinkedIn (post/comment/dm). content_hash
    = sha256(texte normalisé) → empêche la double publication du même contenu (P10.9).
    status durable : published | failed | pending (survit au redémarrage)."""

    __tablename__ = "publications"
    id = Column(String, primary_key=True, default=lambda: _uid("pub"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    approval_id = Column(String, ForeignKey("approvals.id"), nullable=True, index=True)
    kind = Column(String, nullable=False)  # post | comment | dm
    content_hash = Column(String, nullable=False, index=True)
    linkedin_urn = Column(String, nullable=True)
    status = Column(String, nullable=False)  # published | failed | pending
    error_detail = Column(Text, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class LinkedInSyncRun(Base):
    """Trace d'une exécution de sync LinkedIn → CRM Contacts (P12). Une ligne par sync :
    status (running|completed|failed), compteurs (total/imported/updated/skipped),
    fenêtre temporelle (started_at/completed_at) + error_detail si échec. Scopée par tenant,
    rattachée à l'utilisateur qui a déclenché (actor_user_id). Pont P10 (OAuth) ↔ P11 (CRM)."""

    __tablename__ = "linkedin_sync_runs"
    id = Column(String, primary_key=True, default=lambda: _uid("syn"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    status = Column(String, nullable=False)  # running | completed | failed
    total = Column(Integer, default=0, nullable=False)
    imported = Column(Integer, default=0, nullable=False)
    updated = Column(Integer, default=0, nullable=False)
    skipped = Column(Integer, default=0, nullable=False)
    error_detail = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    actor_user_id = Column(String, ForeignKey("users.id"), nullable=True)


# ─── P11 : CRM & prospection ───────────────────────────────────────────────────
# Chaîne : Company (entreprise) → Contact (prospect, rattaché à 0/1 company) →
# Opportunity (affaire en pipeline) + Task + Note (activités liées au contact ou
# à l'opportunité). Segment = filtre sauvegardé (critères JSON). SuppressionList
# = exclusions RGPD (désinscription/bounce/réclamation) — l'import skippe ces emails.
# Tout est scopé par tenant_id. Contact.email est normalisé (lowercase+strip) et
# unique par tenant (dédup). Contact.score = heuristique 0-100 calculée à l'insert/update.


class Company(Base):
    """Entreprise/prospect compte, scopée par tenant. Un contact y rattache (0..n)."""

    __tablename__ = "companies"
    id = Column(String, primary_key=True, default=lambda: _uid("cmp"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    website = Column(String, default="")
    domain = Column(String, default="", index=True)  # dédup entreprise par domaine
    industry = Column(String, default="")
    size = Column(String, default="")  # ex: "1-10", "11-50", "51-200", "201-500", "501-1000", "1000+"
    country = Column(String, default="")
    linkedin_url = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class Contact(Base):
    """Prospect/contact CRM, scopé par tenant. email normalisé (lowercase+strip),
    unique par tenant → dédup à l'import + à la création manuelle. score = heuristique
    0-100 (titre senior, taille entreprise, complétude des données). owner_id =
    commercial attribué (FK users, nullable = non affecté)."""

    __tablename__ = "contacts"
    id = Column(String, primary_key=True, default=lambda: _uid("ctc"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=True, index=True)
    first_name = Column(String, default="", nullable=False)
    last_name = Column(String, default="", nullable=False)
    email = Column(String, nullable=False, index=True)  # normalisé (lowercase+strip)
    phone = Column(String, default="")
    title = Column(String, default="")
    linkedin_url = Column(String, default="")
    score = Column(Integer, default=0, nullable=False)
    status = Column(String, default="new", nullable=False, index=True)
    # new | qualified | contacted | nurture | won | lost
    owner_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    source = Column(String, default="manual", nullable=False)  # manual | import | linkedin
    intent_score = Column(Integer, default=0, nullable=False)  # P27 : somme des IntentSignal.score récents
    enriched_at = Column(DateTime, nullable=True)  # P27 : dernier enrichissement automatique
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_tenant_contact_email"),)


class Opportunity(Base):
    """Affaire en pipeline (Kanban), scopée par tenant. stage = colonne Kanban.
    Probabilité % de closing. value = montant estimé (Integer, dans la currency)."""

    __tablename__ = "opportunities"
    id = Column(String, primary_key=True, default=lambda: _uid("opp"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    contact_id = Column(String, ForeignKey("contacts.id"), nullable=False, index=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=True, index=True)
    title = Column(String, nullable=False)
    value = Column(Integer, default=0, nullable=False)
    currency = Column(String, default="EUR", nullable=False)
    stage = Column(String, default="prospecting", nullable=False, index=True)
    # prospecting | qualified | proposal | negotiation | closed_won | closed_lost
    probability = Column(Integer, default=10, nullable=False)
    expected_close_date = Column(Date, nullable=True)
    owner_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)  # P24 : auto-set quand stage→closed_won (time-to-close)


class Task(Base):
    """Tâche commerciale (call/demo/relance), scopée par tenant. Liée à un contact
    et/ou une opportunité (les deux nullable). assignee_id = responsable (FK users).
    P23 : reminder_at = instant où le scheduler déclenche une Notification in-app (feed) vers
    l'assignee ; reminder_sent évite les doublons (flip True après envoi). completed_at = timestamp
    auto-set quand status→done (analytics de productivité : temps de résolution)."""

    __tablename__ = "tasks"
    id = Column(String, primary_key=True, default=lambda: _uid("tsk"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    contact_id = Column(String, ForeignKey("contacts.id"), nullable=True, index=True)
    opportunity_id = Column(String, ForeignKey("opportunities.id"), nullable=True, index=True)
    title = Column(String, nullable=False)
    due_date = Column(Date, nullable=True, index=True)
    status = Column(String, default="todo", nullable=False, index=True)  # todo | in_progress | done
    priority = Column(String, default="medium", nullable=False)  # low | medium | high
    assignee_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    reminder_at = Column(DateTime, nullable=True, index=True)  # P23 : instant du rappel (scheduler)
    reminder_sent = Column(Boolean, default=False, nullable=False)  # P23 : évite les doublons de rappel
    completed_at = Column(DateTime, nullable=True)  # P23 : auto-set quand status→done
    created_at = Column(DateTime, default=datetime.utcnow)


class Note(Base):
    """Note/compte-rendu libre sur un contact ou une opportunité, scopée par tenant.
    Auteur = user courant (FK users). body en Text (non limité en DB)."""

    __tablename__ = "notes"
    id = Column(String, primary_key=True, default=lambda: _uid("nte"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    contact_id = Column(String, ForeignKey("contacts.id"), nullable=True, index=True)
    opportunity_id = Column(String, ForeignKey("opportunities.id"), nullable=True, index=True)
    body = Column(Text, nullable=False)
    author_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class Segment(Base):
    """Filtre sauvegardé (liste dynamique de contacts), scopé par tenant.
    criteria = JSON : {search, status, company_id, owner_id, score_min, source}."""

    __tablename__ = "segments"
    id = Column(String, primary_key=True, default=lambda: _uid("seg"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    criteria = Column(JSONType, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class SuppressionList(Base):
    """Exclusions RGPD (désinscription/bounce/réclamation), scopées par tenant.
    L'import de contacts skippe les emails présents ici (respect de la désinscription).
    UniqueConstraint(tenant_id, email) → pas de doublon. Hard delete possible (RGPD)."""

    __tablename__ = "suppression_list"
    id = Column(String, primary_key=True, default=lambda: _uid("sup"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    email = Column(String, nullable=False, index=True)  # normalisé (lowercase+strip)
    reason = Column(String, nullable=False)  # unsubscribe | bounced | complaint
    source = Column(String, default="manual", nullable=False)  # manual | import
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_tenant_suppression_email"),)


# ─── P13 : Notifications & e-mails (pont P12 sync ↔ outreach CRM) ─────────────
class Notification(Base):
    """Notification multi-canal (email + in-app feed), scopée par tenant (P13). Trace chaque
    envoi d'e-mail de prospection/suivi + les notifications in-app (cloche dashboard). status :
    pending|sent|failed|dry_run (smtp_enabled=false → dry_run). read_at nullable pour le feed
    in-app (null = non lu). Rattachée au contact CRM (recipient_contact_id) + à l'expéditeur
    (user_id). Pont P12 (contacts syncés LinkedIn) → P13 (outreach e-mail)."""

    __tablename__ = "notifications"
    id = Column(String, primary_key=True, default=lambda: _uid("ntf"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)  # expéditeur
    type = Column(String, nullable=False)  # email | in_app | system
    channel = Column(String, nullable=False, default="email")  # email | feed
    recipient_email = Column(String, nullable=True, index=True)  # normalisé (null pour in-app pur)
    recipient_contact_id = Column(String, ForeignKey("contacts.id"), nullable=True)
    subject = Column(String, nullable=False)
    body_text = Column(Text, nullable=False)
    body_html = Column(Text, nullable=True)
    template_id = Column(String, nullable=True)  # cold_outreach|follow_up|intro|re_engagement
    status = Column(String, nullable=False, default="pending")  # pending|sent|failed|dry_run
    sent_at = Column(DateTime, nullable=True)
    error_detail = Column(Text, nullable=True)
    read_at = Column(DateTime, nullable=True)  # in-app feed : null = non lu
    created_at = Column(DateTime, default=datetime.utcnow)


# ─── P22 : Tags & catégorisation de contacts ─────────────────────────────────
class Tag(Base):
    """Étiquette/catégorie de contact (P22), scopée par tenant. Complète les Segments (P14) qui
    sont des filtres dynamiques par requête : un Tag est un label explicite apposé sur des
    contacts (ex. « VIP », « Cold », « Webinaire 2026 »). UniqueConstraint(tenant_id, name) → pas
    de doublon de nom dans un tenant. color = hex pour le badge frontend. La suppression d'un tag
    cascade-supprime les associations ContactTag (ondelete=CASCADE sur la FK côté association)."""

    __tablename__ = "tags"
    id = Column(String, primary_key=True, default=lambda: _uid("tag"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    color = Column(String, default="#635bff", nullable=False)  # hex badge frontend
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tenant_tag_name"),)


class ContactTag(Base):
    """Association many-to-many Contact ↔ Tag (P22). PK composite (contact_id, tag_id) → pas de
    doublon d'assignation. CASCADE sur les deux FK (suppression d'un contact OU d'un tag nettoie
    les associations). Pas de payload supplémentaire (assignation = présence de la ligne)."""

    __tablename__ = "contact_tags"
    contact_id = Column(String, ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True)
    tag_id = Column(String, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---- Séquences & automatisation (P15) ----
class EmailSequence(Base):
    """Séquence d'e-mails multi-steps (P15). Drip campaign automatisée : enrollment d'un contact →
    envoi échelonné des SequenceStep (delay_days entre steps) → advancement par le scheduler
    (services/sequences.py process_due_enrollments). Scopée par tenant. status :
    draft (non démarrée, scheduler ignore) | active (scheduler traite) | paused (suspendue)."""

    __tablename__ = "email_sequences"
    id = Column(String, primary_key=True, default=lambda: _uid("seq"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    status = Column(String, default="draft", nullable=False)  # draft | active | paused
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SequenceStep(Base):
    """Étape d'une séquence (P15, étendu P27 multicanal). order = position 1-indexed. template_id =
    template e-mail P13 (cold_outreach|follow_up|intro|re_engagement) — NULL pour les steps non-email
    (linkedin, call, sms, manual). delay_days = délai avant envoi après le step précédent (0 = immédiat).
    P27 : channel = email|linkedin|call|sms|manual (default email — backward compat P15). channel_config
    = JSON ({linkedin_action: connection_request|message|profile_view|like_comment, call_script: "...",
    manual_task: "...", sms_text: "..."}). CASCADE sur delete séquence."""

    __tablename__ = "sequence_steps"
    id = Column(String, primary_key=True, default=lambda: _uid("stp"))
    sequence_id = Column(String, ForeignKey("email_sequences.id", ondelete="CASCADE"), nullable=False, index=True)
    step_order = Column(Integer, nullable=False)  # 1-indexed (évite le mot-clé SQL réservé `order`)
    template_id = Column(String, nullable=True)  # P27 : nullable pour steps non-email (linkedin/call/sms/manual)
    delay_days = Column(Integer, default=0, nullable=False)
    channel = Column(String, default="email", nullable=False)  # P27 : email|linkedin|call|sms|manual
    channel_config = Column(JSONType, nullable=True)  # P27 : config spécifique au canal (linkedin_action, call_script...)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SequenceEnrollment(Base):
    """Enrollment d'un contact dans une séquence (P15). current_step_order = prochain step à envoyer
    (1-indexed). status : active (en cours) | completed (dernier step envoyé) | paused | failed.
    next_step_at = quand le scheduler doit envoyer le step courant. enrolled_by_user_id = acteur
    (pour l'audit crm.email_contact via send_email_to_contact). CASCADE sur delete séquence."""

    __tablename__ = "sequence_enrollments"
    id = Column(String, primary_key=True, default=lambda: _uid("enr"))
    sequence_id = Column(String, ForeignKey("email_sequences.id", ondelete="CASCADE"), nullable=False, index=True)
    contact_id = Column(String, ForeignKey("contacts.id"), nullable=False, index=True)
    current_step_order = Column(Integer, default=1, nullable=False)
    status = Column(String, default="active", nullable=False)  # active | completed | paused | failed
    enrolled_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    enrolled_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    next_step_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    error_detail = Column(Text, nullable=True)


# ─── P27 — Prospection avancée : A/B testing + intent signals ────────────────


class SequenceStepVariant(Base):
    """Variante A/B d'un step de séquence (P27 A/B testing). variant = 'A' | 'B'. subject_override +
    body_override = contenu alternatif (si NULL → utilise le template_id du step). sent_count +
    open_count + click_count + reply_count = métriques pour calculer le taux d'ouverture/clic/réponse
    par variante (le router /ab-results agrège ces métriques pour comparer A vs B). CASCADE sur delete
    step."""

    __tablename__ = "sequence_step_variants"
    id = Column(String, primary_key=True, default=lambda: _uid("var"))
    step_id = Column(String, ForeignKey("sequence_steps.id", ondelete="CASCADE"), nullable=False, index=True)
    variant = Column(String, nullable=False)  # A | B
    label = Column(String, nullable=True)  # nom optionnel (e.g. "Version courte", "Ton expert")
    subject_override = Column(String, nullable=True)
    body_override = Column(Text, nullable=True)
    sent_count = Column(Integer, default=0, nullable=False)
    open_count = Column(Integer, default=0, nullable=False)
    click_count = Column(Integer, default=0, nullable=False)
    reply_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class IntentSignal(Base):
    """Signal d'intention d'un contact (P27 détection d'intention). signal_type = job_change|funding|
    hire_spike|news|engagement|website_visit. description = texte libre. score = poids 1-100. source =
    manual|webhook|enrichment. detected_at = quand le signal a été détecté. Utilisé pour calculer
    Contact.intent_score (somme des scores — see services/prospecting.compute_intent_score). CASCADE
    sur delete contact."""

    __tablename__ = "intent_signals"
    id = Column(String, primary_key=True, default=lambda: _uid("isig"))
    tenant_id = Column(String, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    contact_id = Column(String, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True)
    signal_type = Column(String, nullable=False, index=True)  # job_change|funding|hire_spike|news|engagement|website_visit
    description = Column(Text, nullable=True)
    score = Column(Integer, default=10, nullable=False)  # 1-100
    source = Column(String, default="manual", nullable=False)  # manual|webhook|enrichment
    detected_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ---- Webhooks outbound (P18) ----
class Webhook(Base):
    """Webhook outbound (P18) : notifie un système externe sur des événements RevPulse
    (Slack/Zapier/CRM externe). Scopé par tenant. events = liste d'événements souscrits
    (contact.created, contact.scored, sequence.enrolled, sequence.step_completed, email.sent,
    opportunity.created). secret = clé HMAC-SHA256 signant chaque livraison (header
    X-RevPulse-Signature → le destinataire vérifie l'authenticité). active=false → suspendu
    (emit_event skippe). created_by = admin créateur (FK users, nullable)."""

    __tablename__ = "webhooks"
    id = Column(String, primary_key=True, default=lambda: _uid("whk"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    url = Column(String, nullable=False)
    secret = Column(String, nullable=False)  # P24-A : legacy plaintext (lazy-migré vers secret_enc)
    secret_enc = Column(Text, nullable=True)  # P24-A : secret chiffré Fernet (encrypt-at-rest)
    events = Column(JSONType, nullable=False)  # ["contact.created", "contact.scored", ...]
    active = Column(Boolean, default=True, nullable=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class WebhookDelivery(Base):
    """Livraison d'un webhook (P18). payload = JSON envoyé (snap au moment de l'émission).
    status : pending (à livrer / retry en cours) | success (2xx reçu) | failed (épuisé ou
    erreur). attempts = nb d'essais (incrémenté par deliver). max_attempts = 3 (default).
    response_code = code HTTP retourné (null si erreur réseau). last_error = dernier message
    d'erreur (truncé 200 chars). CASCADE sur delete webhook."""

    __tablename__ = "webhook_deliveries"
    id = Column(String, primary_key=True, default=lambda: _uid("whd"))
    webhook_id = Column(String, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
    event = Column(String, nullable=False, index=True)
    payload = Column(JSONType, nullable=False)
    status = Column(String, default="pending", nullable=False, index=True)  # pending|success|failed
    response_code = Column(Integer, nullable=True)
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=3, nullable=False)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    delivered_at = Column(DateTime, nullable=True)


class InboundWebhook(Base):
    """Webhook inbound (P20) : endpoint public POST /api/inbound/webhook/{token} où un système
    externe (formulaire site, Zapier, CRM externe) pousse des données prospect → RevPulse crée
    ou met à jour un Contact. Scopé par tenant. token = identifiant public dans l'URL (32 hex,
    gen_token) — contrairement au secret HMAC outbound (jamais exposé), le token inbound EST
    l'identifiant de route (l'admin le copie dans la config du système source). field_map = dict
    optionnel mappant les clés du payload source → champs Contact (default: identity map sur
    first_name/last_name/email/phone/title/linkedin_url). active=false → 410 Gone (suspendu).
    created_by = admin créateur (FK users, nullable). Symétrique du Webhook outbound (P18)."""

    __tablename__ = "inbound_webhooks"
    id = Column(String, primary_key=True, default=lambda: _uid("ibw"))
    tenant_id = Column(String, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    token = Column(String, nullable=False, unique=True, index=True)  # 32 hex — public, dans l'URL
    source_name = Column(String, nullable=False)  # ex. "Website form", "Zapier", "CRM sync"
    field_map = Column(JSONType, nullable=True)  # dict {payload_key: contact_field} — None = default map
    active = Column(Boolean, default=True, nullable=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class InboundEvent(Base):
    """Événement inbound traité (P24-A — idempotence). Un row par (webhook_id, event_id) reçu :
    empêche le retraitement d'un même événement sur retry/replay (sender qui renvoie sur timeout).
    event_id extrait du header X-Webhook-Id / Webhook-Id OU d'un champ payload event_id/messageId.
    UniqueConstraint(tenant_id, webhook_id, event_id) → IntegrityError sur doublon → skip idempotent.
    payload_hash = sha256 du payload (audit/dedup secondaire). processed_at = timestamp du 1er traitement."""

    __tablename__ = "inbound_events"
    __table_args__ = (UniqueConstraint("tenant_id", "webhook_id", "event_id", name="uq_inbound_event"),)
    id = Column(String, primary_key=True, default=lambda: _uid("ibe"))
    tenant_id = Column(String, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    webhook_id = Column(String, ForeignKey("inbound_webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id = Column(String, nullable=False)  # idempotence — vient du header/payload source
    payload_hash = Column(String, nullable=True)  # sha256 du payload (audit)
    processed_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── P24-B — AI Gateway multi-provider ───────────────────────────────────────


class AIProviderConfig(Base):
    """Config provider IA par organisation (P24-B). provider = openai|anthropic|gemini|deepseek|glm|
    openrouter|ollama. api_key_enc = clé chiffrée Fernet (services/crypto, jamais en clair). enabled
    + priority (ordre de fallback — 0 = primaire). quota_tokens_month + cost_usd_month (reset mensuel
    via tokens_used_month/cost_incurred_month). base_url override (Ollama http://localhost:11434 ou
    proxy d'entreprise). UniqueConstraint (tenant, provider, label) — un tenant peut avoir plusieurs
    configs du même provider (ex. OpenAI prod + OpenAI sandbox)."""

    __tablename__ = "ai_provider_configs"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "label", name="uq_ai_provider_config"),)
    id = Column(String, primary_key=True, default=lambda: _uid("aip"))
    tenant_id = Column(String, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String, nullable=False)  # openai|anthropic|gemini|deepseek|glm|openrouter|ollama
    label = Column(String, nullable=False)  # "OpenAI prod", "Ollama local" — affiché + dédup
    api_key_enc = Column(Text, nullable=True)  # chiffré Fernet (null pour Ollama sans auth)
    base_url = Column(String, nullable=True)  # override (Ollama, proxy entreprise)
    enabled = Column(Boolean, default=True, nullable=False)
    priority = Column(Integer, default=0, nullable=False)  # ordre fallback (0 = primaire)
    quota_tokens_month = Column(Integer, nullable=True)  # null = illimité
    cost_usd_month = Column(Float, nullable=True)  # null = illimité
    tokens_used_month = Column(Integer, default=0, nullable=False)  # reset mensuel
    cost_incurred_month = Column(Float, default=0.0, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    @property
    def has_api_key(self) -> bool:
        """True si une clé API chiffrée est stockée (P24-B). Exposé via AIProviderConfigOut.has_api_key
        (la clé elle-même n'est JAMAIS exposée — seulement ce booléen)."""
        return bool(self.api_key_enc)


class PromptVersion(Base):
    """Prompts versionnés (P24-B). tenant + name + version (unique, incrémental). content = template
    avec {{variables}} (rendu par str.format_map). variables = liste JSON des noms attendus. active =
    un seul actif par (tenant, name) — le gateway utilise la version active. notes = changelog.
    Réutilisé par les agents P25 (qualification, scoring, personnalisation, rédaction LinkedIn…)."""

    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("tenant_id", "name", "version", name="uq_prompt_version"),)
    id = Column(String, primary_key=True, default=lambda: _uid("pmv"))
    tenant_id = Column(String, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)  # "linkedin_post", "email_personalization", "lead_qualification"
    version = Column(Integer, nullable=False)  # incrémental par (tenant, name)
    content = Column(Text, nullable=False)  # template {{variables}}
    variables = Column(JSONType, nullable=True)  # ["first_name", "company", ...]
    active = Column(Boolean, default=True, nullable=False)  # un seul actif par name
    notes = Column(Text, nullable=True)  # changelog de version
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AIGeneration(Base):
    """Historisation des générations IA (P24-B). tenant + provider_config + model + prompt_version +
    input (prompt rendu APRÈS PII redact) + output + tokens_in/out + cost_usd + latency_ms + status.
    simulated=True → provider simulé (jamais présenté comme réel — anti-fake). error = message si
    failed. Audit trail complet pour la conformité + l'analyse de coûts."""

    __tablename__ = "ai_generations"
    id = Column(String, primary_key=True, default=lambda: _uid("aig"))
    tenant_id = Column(String, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_config_id = Column(String, ForeignKey("ai_provider_configs.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String, nullable=False)
    model = Column(String, nullable=False)
    prompt_version_id = Column(String, ForeignKey("prompt_versions.id"), nullable=True)
    prompt_name = Column(String, nullable=True)
    input_text = Column(Text, nullable=False)  # prompt rendu (après PII redact)
    output_text = Column(Text, nullable=True)
    tokens_in = Column(Integer, nullable=True)
    tokens_out = Column(Integer, nullable=True)
    cost_usd = Column(Float, default=0.0, nullable=False)
    latency_ms = Column(Integer, nullable=True)
    status = Column(String, default="pending", nullable=False)  # pending|success|failed|simulated
    simulated = Column(Boolean, default=False, nullable=False)  # True si provider simulé
    error = Column(Text, nullable=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# ─── P25 — Agents IA spécialisés + AI Copilot ─────────────────────────────────


class AgentRun(Base):
    """Historisation des runs d'agents IA (P25). tenant + agent name (qualification|scoring|research|
    personalization|linkedin|competitive|meeting_prep|performance|recommendation|synthesis) +
    context (JSON : contact_id, opportunity_id, message, etc.) + result_text + status + simulated
    (propagé depuis l'AI Gateway) + require_human_validation (si injection détectée OU action externe
    proposée). generation_id = lien vers l'AIGeneration sous-jacente (audit trail). created_by = user
    qui a déclenché le run (via Copilot OU endpoint direct)."""

    __tablename__ = "agent_runs"
    id = Column(String, primary_key=True, default=lambda: _uid("agr"))
    tenant_id = Column(String, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    agent = Column(String, nullable=False, index=True)  # qualification|scoring|research|...
    context = Column(JSONType, nullable=True)  # {contact_id, opportunity_id, message, ...}
    result_text = Column(Text, nullable=True)
    status = Column(String, default="pending", nullable=False)  # pending|success|simulated|failed
    simulated = Column(Boolean, default=False, nullable=False)  # propagé depuis gateway
    require_human_validation = Column(Boolean, default=False, nullable=False)  # injection OU action externe
    generation_id = Column(String, ForeignKey("ai_generations.id"), nullable=True)  # audit trail
    error = Column(Text, nullable=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# ─── P26 — LinkedIn Content Studio professionnel ─────────────────────────────


class ContentTemplate(Base):
    """Modèle de post LinkedIn réutilisable (P26 Content Studio). category = storytelling|insight|
    announcement|carousel|question|behind_the_scenes. structure = template avec {{variables}} (hook,
    corps, CTA). style = ton (expert, terrain, data-driven). is_system=True pour les modèles seedés
    (non modifiables/supprimables par les tenants — seed_content_templates au 1er accès)."""

    __tablename__ = "content_templates"
    id = Column(String, primary_key=True, default=lambda: _uid("ctpl"))
    tenant_id = Column(String, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False, index=True)  # storytelling|insight|announcement|carousel|question|bts
    structure = Column(Text, nullable=False)  # template with {{variables}}
    variables = Column(JSONType, nullable=True)  # list of variable names ["topic", "style", ...]
    style = Column(String, nullable=True)
    is_system = Column(Boolean, default=False, nullable=False)  # system templates (seeded, immutable)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class ContentPost(Base):
    """Post LinkedIn Content Studio (P26). Workflow validation collaborative :
    draft → pending_review → approved|rejected → scheduled → published.
    content_text = texte du post (généré via LinkedInAgent P25 OU rédigé manuellement). carousel =
    JSON (slides [{order, text, image_url}]). topic + style + hashtags = inputs de génération.
    scheduled_at = planification. published_at + linkedin_urn = publication (via LinkedIn API P12,
    require approvals.manage — action externe). ai_generation_id = lien vers l'AIGeneration P24-B
    (audit trail P25→P26). validation_requested_by/at + validated_by/at + rejected_reason = workflow.
    linkedin_account_id = compte LinkedIn publié (P12). stats = JSON (impressions, likes, comments,
    shares — via LinkedIn API stats sync, P26 v1 : NULL jusqu'à sync réelle)."""

    __tablename__ = "content_posts"
    id = Column(String, primary_key=True, default=lambda: _uid("cpst"))
    tenant_id = Column(String, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=True)  # titre interne (non publié)
    content_text = Column(Text, nullable=False)
    topic = Column(String, nullable=True)
    style = Column(String, nullable=True)
    hashtags = Column(String, nullable=True)  # comma-separated
    carousel = Column(JSONType, nullable=True)  # [{order, text, image_url}]
    media_urls = Column(JSONType, nullable=True)  # [url, ...]
    template_id = Column(String, ForeignKey("content_templates.id"), nullable=True)
    status = Column(String, default="draft", nullable=False, index=True)  # draft|pending_review|approved|rejected|scheduled|published
    scheduled_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    linkedin_urn = Column(String, nullable=True)  # URN returned by LinkedIn API
    ai_generation_id = Column(String, ForeignKey("ai_generations.id"), nullable=True)
    validation_requested_by = Column(String, ForeignKey("users.id"), nullable=True)
    validation_requested_at = Column(DateTime, nullable=True)
    validated_by = Column(String, ForeignKey("users.id"), nullable=True)
    validated_at = Column(DateTime, nullable=True)
    rejected_reason = Column(Text, nullable=True)
    linkedin_account_id = Column(String, nullable=True)  # FK LinkedIn account (P12+ OAuth — table à venir, FK ajouté quand LinkedInAccount model créé)
    stats = Column(JSONType, nullable=True)  # {impressions, likes, comments, shares}
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# ─── P28 — Warmup inbox + Sales Intelligence ─────────────────────────────────


class InboxWarmup(Base):
    """Configuration de warmup inbox (P28). Gradation du volume d'envoi email pour construire la
    réputation de l'expéditeur (sender reputation) avant d'envoyer des cold emails à volume élevé —
    évite les bounces/spam-folder des nouveaux domaines. sender_email = adresse à warmer.
    target_volume = volume quotidien cible (e.g. 100 emails/jour). ramp_days = durée de la rampe
    (default 30j). current_day = jour actuel (0 = pas commencé, 1..ramp_days = en cours).
    daily_volume = volume calculé (linéaire : day 1 → ~3, day ramp_days → target_volume). ramp_status :
    pending (créé, non démarré) | ramping (en cours, current_day < ramp_days) | active (current_day >=
    ramp_days, daily_volume = target_volume) | paused | completed. CASCADE sur delete tenant."""

    __tablename__ = "inbox_warmups"
    id = Column(String, primary_key=True, default=lambda: _uid("wrm"))
    tenant_id = Column(String, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_email = Column(String, nullable=False)
    target_volume = Column(Integer, default=100, nullable=False)
    ramp_days = Column(Integer, default=30, nullable=False)
    current_day = Column(Integer, default=0, nullable=False)
    daily_volume = Column(Integer, default=0, nullable=False)
    ramp_status = Column(String, default="pending", nullable=False, index=True)  # pending|ramping|active|paused|completed
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WarmupMessage(Base):
    """Message de warmup (P28). Enregistré pour tracker la réputation (open/reply rate) pendant la
    rampe — un volume d'envoi sans opens/replies signale un domaine spammy. sent_at = quand envoyé.
    opened = si ouvert (bool, simule un open-tracking pixel). replied = si répondu (bool, simule une
    interaction humaine). CASCADE sur delete warmup."""

    __tablename__ = "warmup_messages"
    id = Column(String, primary_key=True, default=lambda: _uid("wm"))
    warmup_id = Column(String, ForeignKey("inbox_warmups.id", ondelete="CASCADE"), nullable=False, index=True)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    opened = Column(Boolean, default=False, nullable=False)
    replied = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── P34 — Career : référentiel métiers + CV (espace candidat isolé du Business) ─────────────


class CareerProfile(Base):
    """Profil Career d'un candidat (P34). Espace personnel ISOLÉ du Business tenant — un user peut
    être Member d'un tenant Business (côté prospecteur) ET avoir un CareerProfile (côté candidat) ;
    les données Career ne sont JAMAIS accessibles via le tenant Business (pas de tenant_id, scoped par
    user_id unique). target_job_title = métier visé (ex. "Product Owner"). contract_types = JSON array
    (CDI|CDD|freelance|stage|alternance). salary_min/max + tjm_min + currency = préférences rémunération.
    mobility = accepte relocalisation. languages = JSON array (fr|en|es...). CASCADE sur delete user."""

    __tablename__ = "career_profiles"
    id = Column(String, primary_key=True, default=lambda: _uid("crp"))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    target_job_title = Column(String, nullable=True)  # métier visé (libre OU code ROME/ESCO)
    preferred_location = Column(String, nullable=True)  # ex. "Paris, France" ou "remote"
    mobility = Column(Boolean, default=False, nullable=False)  # accepte relocalisation
    contract_types = Column(JSONType, nullable=True)  # ["CDI","CDD","freelance"]
    salary_min = Column(Integer, nullable=True)  # rémunération min (annuel brut, EUR par défaut)
    salary_max = Column(Integer, nullable=True)
    tjm_min = Column(Integer, nullable=True)  # TJM min (freelance, EUR/jour)
    currency = Column(String, default="EUR", nullable=False)
    languages = Column(JSONType, nullable=True)  # ["fr","en","es"]
    # P40 — préférences notification (opt-in explicite anti-spam) :
    alert_enabled = Column(Boolean, default=True, nullable=False)  # opt-in global (désactivé → aucune notif)
    alert_min_score = Column(Integer, default=70, nullable=False)  # seuil P36 déclenchant new_match (0-100)
    alert_channels = Column(JSONType, nullable=True)  # ["in_app","email"] — canaux activés
    alert_frequency = Column(String, default="daily", nullable=False)  # daily|weekly|instant
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class CV(Base):
    """CV d'un candidat (P34). career_profile_id FK (CASCADE). source_type = text|pdf|docx (import).
    raw_text = texte extrait (pdfminer pour PDF, python-docx pour Word). parsed_data = JSON structuré
    (skills, experiences, certifications, education) extrait par services/career_cv.parse_cv. version =
    auto-incrément par profile (versioning). is_active = le CV actif (1 seul par profile — set_active
    désactive les autres). locale = langue du CV (fr|en...)."""

    __tablename__ = "cvs"
    id = Column(String, primary_key=True, default=lambda: _uid("cv"))
    career_profile_id = Column(String, ForeignKey("career_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)  # libellé affiché (ex. "CV Product Owner 2026")
    source_type = Column(String, nullable=False)  # text|pdf|docx
    source_filename = Column(String, nullable=True)  # nom du fichier original (pdf/docx)
    raw_text = Column(Text, nullable=False)  # texte extrait (jamais le binaire)
    locale = Column(String, default="fr", nullable=False)  # fr|en|es...
    version = Column(Integer, default=1, nullable=False)  # versionning par profile
    is_active = Column(Boolean, default=False, nullable=False, index=True)
    parsed_data = Column(JSONType, nullable=True)  # {skills:[], experiences:[], certifications:[], education:[]}
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class JobReference(Base):
    """Référentiel métiers universel (P34). code = identifiant (ROME "M1805", ESCO "...", O*NET "15-1131").
    source = rome|esco|onet. label = libellé métier. category = domaine (finance|product|data|...).
    locale = fr|en. UniqueConstraint (code, source) — un même code existe une fois par source. Seeded
    via services/career_referential.seed_referential (subset MVP — v2 : import complet ROME/ESCO/O*NET
    selon licences)."""

    __tablename__ = "job_references"
    __table_args__ = (UniqueConstraint("code", "source", name="uq_job_reference_code_source"),)
    id = Column(String, primary_key=True, default=lambda: _uid("jrf"))
    code = Column(String, nullable=False, index=True)  # ROME/ESCO/O*NET code
    source = Column(String, nullable=False)  # rome|esco|onet
    label = Column(String, nullable=False)  # libellé métier
    category = Column(String, nullable=False)  # finance|product|data|cyber|rh|...
    locale = Column(String, default="fr", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── P35 — Career : recherche d'opportunités (connecteurs + postings + saved searches) ──────


class JobPosting(Base):
    """Offre d'emploi cacheée issue d'un connecteur (P35). source_connector = linkedin_jobs|freework|
    malt|lehibou|france_travail|apec|indeed|hays|wttj|upwork|career_sites|africa (services/job_connectors).
    dedup_hash = sha256(title+company+location normalisé) — détecte les doublons cross-connecteurs.
    expires_at = date d'expiration (depuis le connecteur) OU null (heuristic expired via posted_at + âge).
    raw_data = JSON brut du connecteur (pour audit/re-parse). fetched_at = dernière fois vu actif.
    UniqueConstraint (dedup_hash, source_connector) — une même offre peut exister chez plusieurs sources."""

    __tablename__ = "job_postings"
    __table_args__ = (UniqueConstraint("dedup_hash", "source_connector", name="uq_job_posting_dedup_source"),)
    id = Column(String, primary_key=True, default=lambda: _uid("jbp"))
    dedup_hash = Column(String, nullable=False, index=True)  # sha256(title+company+location)
    source_connector = Column(String, nullable=False, index=True)  # linkedin_jobs|freework|malt|...
    external_id = Column(String, nullable=True)  # ID chez le connecteur (pour refresh/expired)
    title = Column(String, nullable=False)
    company = Column(String, nullable=True)
    location = Column(String, nullable=True)
    url = Column(String, nullable=True)  # lien vers l'offre (tracking du connecteur si requis)
    description = Column(Text, nullable=True)  # description courte (snippet)
    contract_type = Column(String, nullable=True)  # CDI|CDD|freelance|stage|alternance
    salary_min = Column(Integer, nullable=True)
    salary_max = Column(Integer, nullable=True)
    currency = Column(String, default="EUR", nullable=False)
    posted_at = Column(DateTime, nullable=True)  # date de publication (chez le connecteur)
    expires_at = Column(DateTime, nullable=True)  # date d'expiration (chez le connecteur, null si inconnu)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False)  # dernière fois vu actif
    is_expired = Column(Boolean, default=False, nullable=False, index=True)  # marqué expiré (heuristic ou refresh)
    raw_data = Column(JSONType, nullable=True)  # JSON brut du connecteur (audit/re-parse)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SavedSearch(Base):
    """Recherche sauvegardée d'un candidat (P35). career_profile_id FK (CASCADE). criteria = JSON
    {keywords, location, contract_types, salary_min/max, radius_km, source_connectors}. alert_enabled +
    alert_frequency (daily|weekly) → déclenche une recherche périodique (v2 : scheduler alertes P40).
    last_run_at + last_run_postings_count pour suivi. Une saved search est ré-exécutable via POST /run."""

    __tablename__ = "saved_searches"
    id = Column(String, primary_key=True, default=lambda: _uid("jss"))
    career_profile_id = Column(String, ForeignKey("career_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)  # libellé affiché (ex. "PO Paris CDI")
    criteria = Column(JSONType, nullable=False)  # {keywords, location, contract_types, salary_min, ...}
    alert_enabled = Column(Boolean, default=False, nullable=False)
    alert_frequency = Column(String, default="daily", nullable=False)  # daily|weekly
    last_run_at = Column(DateTime, nullable=True)
    last_run_postings_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# ─── P37 — Career : CV adaptatif (génération versions ciblées par offre) ──────


class AdaptedCV(Base):
    """CV adapté à une offre cible (P37). Généré à partir d'un CV source (cv_source_id FK CASCADE) via
    cv_adapter.adapt_cv : réordonne les skills par pertinence (P36 matching), sélectionne les expériences
    les plus alignées, génère un résumé objectif ciblé. posting_id FK nullable (si adaptation pour une
    offre spécifique) OU NULL (adaptation pour critères libres).

    Invariant P37 (anti-invention) : adapted_data ne contient QUE du contenu issu du CV source (réordonné,
    sélectionné, résumé). JAMAIS d'invention de skills/expériences/certifications. Le résumé est construit
    à partir des keywords du CV (pas généré de toutes pièces). Vérifié par cv_adapter._assert_no_invention.

    adapted_data structure : {targeted_title, summary, reordered_skills, selected_experiences,
    matched_certifications, missing_skills, match_score}. Téléchargeable en PDF/Word via cv_generator."""

    __tablename__ = "adapted_cvs"
    id = Column(String, primary_key=True, default=lambda: _uid("acv"))
    cv_source_id = Column(String, ForeignKey("cvs.id", ondelete="CASCADE"), nullable=False, index=True)
    career_profile_id = Column(String, ForeignKey("career_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    posting_id = Column(String, ForeignKey("job_postings.id", ondelete="SET NULL"), nullable=True, index=True)
    target_job_title = Column(String, nullable=True)  # poste visé (posting.title OU criteria)
    target_company = Column(String, nullable=True)  # entreprise cible (posting.company OU criteria)
    adapted_data = Column(JSONType, nullable=False)  # structure adaptée (anti-invention)
    match_score = Column(Float, nullable=True)  # score P36 matching (référence)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── P38 — Career : Candidatures + Gmail/Outlook (pipeline de candidatures) ────


class JobApplication(Base):
    """Candidature à une offre (P38). Pipeline de suivi : submitted → interview → offer/rejected/withdrawn.
    career_profile_id FK CASCADE (isolation candidat), posting_id FK SET NULL (nullable — peut candidater
    hors offre connue), adapted_cv_id FK SET NULL (nullable — CV adapté utilisé si spécifié).

    cover_letter : lettre de motivation générée via cover_letter.generate_cover_letter (anti-invention
    P37 invariant — n'ajoute QUE ce qui est dans le CV source). timeline : JSON [{step, status, date, note}]
    (historique des étapes + dates).

    Anti-spam (P38 invariant) : envoi email via email_sender (OAuth2 Gmail/Outlook officiel — v1 stub
    not_configured comme P35 connectors). JAMAIS de mass-email/spam — une candidature à la fois avec
    consentement explicite du candidat (POST /applications/{id}/send). Pas d'automatisation de relance
    non consentie (v2 : scheduler relances opt-in)."""

    __tablename__ = "job_applications"
    id = Column(String, primary_key=True, default=lambda: _uid("app"))
    career_profile_id = Column(String, ForeignKey("career_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    posting_id = Column(String, ForeignKey("job_postings.id", ondelete="SET NULL"), nullable=True, index=True)
    adapted_cv_id = Column(String, ForeignKey("adapted_cvs.id", ondelete="SET NULL"), nullable=True)
    company = Column(String, nullable=False)  # entreprise cible (posting.company OU saisie libre)
    job_title = Column(String, nullable=False)  # poste visé (posting.title OU saisie libre)
    status = Column(String, default="submitted", nullable=False, index=True)
    # submitted|interview|offer|rejected|withdrawn
    cover_letter = Column(Text, nullable=True)  # lettre générée (anti-invention P37 invariant)
    application_method = Column(String, default="manual", nullable=False)  # manual|email|external
    recipient_email = Column(String, nullable=True)  # email destinataire si envoi via email_sender
    sent_at = Column(DateTime, nullable=True)  # date d'envoi effectif (si email_sender)
    timeline = Column(JSONType, nullable=True)  # [{step, status, date, note}]
    notes = Column(Text, nullable=True)  # notes libres du candidat
    applied_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class CareerNotification(Base):
    """Notification Career (P40) — ISOLÉE du Business Notification (tenant_id). user_id + career_profile_id
    FK CASCADE (isolation stricte Career/Business, P34 invariant). type : new_match (offre matchant le
    profil, score P36 ≥ alert_min_score) | application_status (changement statut candidature
    interview/offer/rejected) | interview_reminder (rappel entretien à venir). channel : email | in_app.
    payload JSON : {posting_id, application_id, score, message, ...}. read_at nullable (null = non lu).

    Anti-spam (P40 invariant) : alert_enabled (opt-in explicite sur CareerProfile) + dedup par
    (user_id, type, payload.key) — une seule notif par posting/application/event. JAMAIS de notification
    si alert_enabled=False. v2 : scheduler alertes périodiques (daily/weekly) + envoi email réel."""

    __tablename__ = "career_notifications"
    id = Column(String, primary_key=True, default=lambda: _uid("cnt"))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    career_profile_id = Column(String, ForeignKey("career_profiles.id", ondelete="CASCADE"), nullable=False)
    type = Column(String, nullable=False, index=True)  # new_match|application_status|interview_reminder
    channel = Column(String, nullable=False, default="in_app")  # email|in_app
    title = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    payload = Column(JSONType, nullable=True)  # {posting_id, application_id, score, message, interview_at}
    read_at = Column(DateTime, nullable=True, index=True)  # null = non lu
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class InterviewSession(Base):
    """Session de simulation d'entretien Career (P41). career_profile_id FK CASCADE (isolation) +
    application_id FK SET NULL (lien optionnel à une candidature dont on prépare l'entretien).
    session_type : tech (questions techniques) | rhmixed (RH + tech mixte). status : in_progress|completed.
    questions JSON [{id, text, type, topic, expected_keywords}]. answers JSON [{question_id, text, score,
    feedback, matched, missing}]. feedback JSON (récap global + skills manquantes + formations). score 0-100.

    Anti-invention (P41 invariant) : questions + feedback basés sur référentiel P34 + CV parsed_data —
    aucune affirmation non vérifiable sur le candidat. analyze_answer = heuristic keywords (pas de note
    arbitraire). recommend_missing_skills cross-référence CV vs métier (transparence)."""

    __tablename__ = "interview_sessions"
    id = Column(String, primary_key=True, default=lambda: _uid("int"))
    career_profile_id = Column(String, ForeignKey("career_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    application_id = Column(String, ForeignKey("job_applications.id", ondelete="SET NULL"), nullable=True)
    target_job_title = Column(String, nullable=False)
    category = Column(String, nullable=True)  # data|product|finance|cyber|rh|...
    session_type = Column(String, nullable=False)  # tech|rhmixed
    status = Column(String, default="in_progress", nullable=False, index=True)  # in_progress|completed
    questions = Column(JSONType, nullable=True)  # [{id, text, type, topic, expected_keywords}]
    answers = Column(JSONType, nullable=True)  # [{question_id, text, score, feedback, matched, missing}]
    feedback = Column(JSONType, nullable=True)  # récap global + missing_skills + formations + negotiation
    score = Column(Integer, nullable=True)  # 0-100 (score global, nullable tant que in_progress)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
