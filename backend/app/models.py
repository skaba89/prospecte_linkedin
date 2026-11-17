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

    ⚠️ access_token stocké en clair pour la démo — à chiffrer (Fernet) en production
    et ne JAMAIS exposer dans une réponse API (status renvoie seulement member_name/id).
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
