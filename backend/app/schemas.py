"""Schémas Pydantic (validation + sérialisation)."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ---- Auth ----
class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=4, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    name: str
    created_at: datetime | None = None


class Token(BaseModel):
    token: str
    user: UserOut


# ---- Tenants ----
class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    plan: str = "Starter"


class TenantRename(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    type: str
    name: str
    headline: str
    company: str
    followers: int
    initials: str


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    plan: str
    owner_id: str
    created_at: datetime
    role: str | None = None
    profile: ProfileOut | None = None
    member_count: int = 0


# ---- Members / RBAC ----
class InviteCreate(BaseModel):
    email: EmailStr
    name: str = Field(default="", max_length=80)
    role: str = "Viewer"


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    email: str
    name: str
    role: str
    status: str
    user_id: str | None = None  # P23 : exposé pour le dropdown assignee de Task (FK users.id)
    token: str | None = None
    invited_by: str | None = None
    added_at: datetime
    expires_at: datetime | None = None


class InviteResult(MemberOut):
    """Résultat d'une invitation : le membre créé + le statut d'envoi de l'e-mail."""

    email_sent: bool = True


class RoleUpdate(BaseModel):
    role: str


# ---- Approvals ----
class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    title: str
    submitter: str
    submitted_ago: str
    priority: str
    status: str
    risk_flags: str
    summary: str
    content_body: str = ""
    type_label: str
    type_color: str
    priority_label: str
    priority_color: str
    status_label: str
    status_color: str
    approved: bool


# ---- Dashboard ----
class ModuleOut(BaseModel):
    module_key: str
    payload: Any


class BillingOut(BaseModel):
    plan: str
    seats: int
    member_count: int
    usage_pct: float
    # P30 — quota IA + devise
    ia_calls_used: int = 0
    ia_calls_limit: int | None = None  # None = illimité (Enterprise)
    currency: str = "EUR"


class PlanUpdate(BaseModel):
    plan: str


class SubscriptionOut(BaseModel):
    plan: str
    status: str
    ia_calls_used: int
    ia_calls_limit: int | None = None
    ia_calls_reset_at: str | None = None
    current_period_end: str | None = None
    stripe_customer_id: str | None = None
    simulated: bool = True  # True si pas de Stripe configuré (mock)


class CheckoutRequest(BaseModel):
    target_plan: str
    success_url: str = "https://revpulse.io/billing?status=success"
    cancel_url: str = "https://revpulse.io/billing?status=cancel"


class CheckoutOut(BaseModel):
    checkout_url: str | None = None
    session_id: str | None = None
    simulated: bool = True
    upgraded_directly: bool = False
    plan: str | None = None
    message: str | None = None


class WebhookResult(BaseModel):
    processed: bool = False
    type: str = ""
    tenant_id: str | None = None
    plan: str | None = None
    error: str | None = None


class CurrencyUpdate(BaseModel):
    currency: str  # EUR|USD|GNF|XOF


# ---- Content (authoring) ----
class ContentCreate(BaseModel):
    type: str = "post"  # post | dm | comment
    title: str = Field(min_length=1, max_length=200)
    content_body: str = Field(default="", max_length=5000)
    priority: str = "medium"  # high | medium | low
    submitter: str = Field(default="", max_length=80)
    summary: str = Field(default="", max_length=300)


# ---- LinkedIn ----
class LinkedInAuthUrl(BaseModel):
    auth_url: str
    mock: bool = False


class LinkedInStatus(BaseModel):
    connected: bool = False
    member_name: str = ""
    member_id: str = ""
    member_headline: str = ""
    expires_at: str | None = None
    mock: bool = False


class PublishResult(BaseModel):
    published: bool
    post_urn: str = ""
    detail: str = ""


class SyncResult(BaseModel):
    """Résultat d'une sync LinkedIn → CRM (P12) : compteurs + id du run pour traçabilité."""

    sync_run_id: str
    status: str  # completed | failed
    total: int = 0
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    detail: str = ""


class SyncRunOut(BaseModel):
    """Ligne d'historique d'un sync run (P12) — pour GET /linkedin/sync-runs."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    status: str
    total: int = 0
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None


# ---- Notifications & e-mails (P13) ----
class NotificationOut(BaseModel):
    """Notification (e-mail envoyé ou in-app feed), scopée par tenant (P13)."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    channel: str = "email"
    recipient_email: str | None = None
    recipient_contact_id: str | None = None
    subject: str
    body_text: str = ""
    body_html: str | None = None
    template_id: str | None = None
    status: str  # pending|sent|failed|dry_run
    sent_at: datetime | None = None
    error_detail: str | None = None
    read_at: datetime | None = None
    created_at: datetime | None = None


class EmailSendRequest(BaseModel):
    """Envoi d'un e-mail templated à un contact CRM (P13). template + message perso optionnel."""

    template: str  # cold_outreach | follow_up | intro | re_engagement
    custom_message: str = ""


class TemplateInfo(BaseModel):
    id: str
    name: str
    description: str


class EmailSendResult(BaseModel):
    """Résultat d'un envoi e-mail (P13) : la notification créée + statut d'envoi.
    notification_id nullable (None si template inconnu — pas de notif créée, helper P15 send_email_to_contact)."""

    notification_id: str | None = None
    status: str  # sent | failed | dry_run | skipped
    email_sent: bool
    detail: str = ""


# ---- Audit (P4) ----
class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    actor_user_id: str | None = None
    actor_name: str = ""
    action: str
    target_type: str = ""
    target_id: str = ""
    detail: dict[str, Any] | list | None = None
    request_id: str = ""
    # P48 : IP + User-Agent capturés via context vars (RequestLoggingMiddleware) → SOC2/GDPR.
    ip: str = ""
    user_agent: str = ""
    created_at: datetime | None = None


# ─── P53 : Audit log retention + export PDF signé + alertes compliance ───
class PurgeResult(BaseModel):
    """Résultat d'une purge de rétention : nombre d'entries supprimées + borne de purge."""
    purged: int
    retention_days: int
    purged_before: datetime  # borne created_at < purged_before → supprimées
    remaining: int  # entries restantes après purge


class AuditAlertItem(BaseModel):
    """Alerte compliance (P53) : anomalie détectée sur l'audit log d'un tenant."""
    kind: str  # "ip_burst" (IP >N actions/heure) | "no_actor" (action sans acteur = système/anonyme)
    severity: str  # "low" | "medium" | "high"
    label: str  # IP ou "actions système"
    count: int
    detail: dict | None = None


class AuditAlertsOut(BaseModel):
    """Ensemble d'alertes compliance (P53) : anomalies sur la fenêtre analysée."""
    window_hours: int
    total_alerts: int
    alerts: list[AuditAlertItem]


# ---- P21 : Audit & compliance UI (stats + filtres + export) ----
class AuditCountItem(BaseModel):
    """Item de comptage agrégé (P21) : label (action ou actor_name) + nombre d'occurrences.
    Utilisé dans AuditStatsOut.by_action / by_actor (top N trié décroissant)."""

    label: str
    count: int


class AuditStatsOut(BaseModel):
    """Stats agrégées de l'audit (P21) : total des entrées du tenant + top actions (group by
    action) + top acteurs (group by actor_name). Supporte le résumé compliance du dashboard
    audit.html (badges « 47 actions, top: crm.contact.create (12), webhook.create (3)…»)."""

    total: int
    by_action: list[AuditCountItem]
    by_actor: list[AuditCountItem]


# ---- P11 : CRM & prospection ----
class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    website: str = Field(default="", max_length=200)
    domain: str = Field(default="", max_length=120)
    industry: str = Field(default="", max_length=80)
    size: str = Field(default="", max_length=40)
    country: str = Field(default="", max_length=60)
    linkedin_url: str = Field(default="", max_length=200)


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    name: str
    website: str = ""
    domain: str = ""
    industry: str = ""
    size: str = ""
    country: str = ""
    linkedin_url: str = ""
    created_at: datetime | None = None


class ContactCreate(BaseModel):
    first_name: str = Field(default="", max_length=80)
    last_name: str = Field(default="", max_length=80)
    email: EmailStr
    phone: str = Field(default="", max_length=40)
    title: str = Field(default="", max_length=120)
    linkedin_url: str = Field(default="", max_length=200)
    company_id: str | None = None
    owner_id: str | None = None
    status: str = "new"  # new | qualified | contacted | nurture | won | lost
    source: str = "manual"  # manual | import | linkedin


class ContactUpdate(BaseModel):
    """Mise à jour partielle : tous les champs optionnels (seuls ceux fournis sont appliqués)."""

    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    title: str | None = None
    linkedin_url: str | None = None
    company_id: str | None = None
    owner_id: str | None = None
    status: str | None = None
    source: str | None = None


# ---- P22 : Tags & catégorisation de contacts ----
class TagCreate(BaseModel):
    name: str
    color: str = "#635bff"


class TagUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    name: str
    color: str = "#635bff"
    created_at: datetime | None = None
    contact_count: int = 0  # P22 : peuplé dans GET /tags (count ContactTag par tag)


class ContactTagAssign(BaseModel):
    """Body pour POST /contacts/{cid}/tags (P22) — assigne une liste de tag_ids à un contact.
    Idempotent : les associations déjà existantes sont skipées (PK composite contact_id+tag_id)."""

    tag_ids: list[str]


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    company_id: str | None = None
    first_name: str = ""
    last_name: str = ""
    email: str
    phone: str = ""
    title: str = ""
    linkedin_url: str = ""
    score: int = 0
    status: str = "new"
    owner_id: str | None = None
    source: str = "manual"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tags: list[TagOut] = []  # P22 : peuplé par _attach_tags (batch query, pas de relationship ORM)


class OpportunityCreate(BaseModel):
    contact_id: str
    company_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    value: int = Field(default=0, ge=0)
    currency: str = Field(default="EUR", max_length=8)
    stage: str = "prospecting"  # prospecting | qualified | proposal | negotiation | closed_won | closed_lost
    probability: int = Field(default=10, ge=0, le=100)
    expected_close_date: date | None = None  # date ISO (YYYY-MM-DD)
    owner_id: str | None = None


class OpportunityUpdate(BaseModel):
    contact_id: str | None = None
    company_id: str | None = None
    title: str | None = None
    value: int | None = Field(default=None, ge=0)
    currency: str | None = None
    stage: str | None = None
    probability: int | None = Field(default=None, ge=0, le=100)
    expected_close_date: date | None = None
    owner_id: str | None = None


class OpportunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    contact_id: str
    company_id: str | None = None
    title: str
    value: int = 0
    currency: str = "EUR"
    stage: str = "prospecting"
    probability: int = 10
    expected_close_date: date | None = None
    owner_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None  # P24 : auto-set quand stage→closed_won (time-to-close)


class StageUpdate(BaseModel):
    """Déplacement d'une opportunité entre colonnes Kanban (PATCH /opportunities/{id}/stage)."""

    stage: str


class TaskCreate(BaseModel):
    contact_id: str | None = None
    opportunity_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    due_date: date | None = None
    status: str = "todo"  # todo | in_progress | done
    priority: str = "medium"  # low | medium | high
    assignee_id: str | None = None
    reminder_at: datetime | None = None  # P23 : instant du rappel (scheduler → Notification)


class TaskUpdate(BaseModel):
    contact_id: str | None = None
    opportunity_id: str | None = None
    title: str | None = None
    due_date: date | None = None
    status: str | None = None
    priority: str | None = None
    assignee_id: str | None = None
    reminder_at: datetime | None = None  # P23 : update du rappel (reset reminder_sent=False si changé)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    contact_id: str | None = None
    opportunity_id: str | None = None
    title: str
    due_date: date | None = None
    status: str = "todo"
    priority: str = "medium"
    assignee_id: str | None = None
    reminder_at: datetime | None = None  # P23
    reminder_sent: bool = False  # P23
    completed_at: datetime | None = None  # P23
    created_at: datetime | None = None


class NoteCreate(BaseModel):
    contact_id: str | None = None
    opportunity_id: str | None = None
    body: str = Field(min_length=1, max_length=5000)


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    contact_id: str | None = None
    opportunity_id: str | None = None
    body: str
    author_id: str | None = None
    created_at: datetime | None = None


class SegmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    criteria: dict[str, Any] = {}  # {search, status, company_id, owner_id, score_min, source}


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    name: str
    criteria: Any = {}
    created_at: datetime | None = None


class SuppressionCreate(BaseModel):
    email: EmailStr
    reason: str = "unsubscribe"  # unsubscribe | bounced | complaint
    source: str = "manual"  # manual | import


class SuppressionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    email: str
    reason: str
    source: str = "manual"
    created_at: datetime | None = None


class ImportRowError(BaseModel):
    row: int
    email: str = ""
    error: str


class ImportResult(BaseModel):
    """Résultat d'un import CSV/XLSX de contacts : créés, doublons (skip), supprimés
    (RGPD — dans la suppression list), erreurs (lignes invalides)."""

    created: int = 0
    duplicates: int = 0
    suppressed: int = 0
    errors: list[ImportRowError] = []
    total: int = 0


class ScoreBreakdown(BaseModel):
    """Détail du scoring d'un contact (heuristique 0-100)."""

    score: int
    breakdown: dict[str, int]


class ScoreRecomputeResult(BaseModel):
    """Résultat du batch recompute (P17) : nb de contacts recalculés + score moyen."""

    recomputed: int
    avg_score: float


class PipelineColumn(BaseModel):
    """Colonne Kanban : stage + opportunités + total valeur."""

    stage: str
    label: str
    color: str
    opportunities: list[OpportunityOut]
    count: int
    total_value: int


# ---- Analytics & reporting (P14) ----
class PipelineStageStat(BaseModel):
    """Stats agrégées d'un stage de pipeline (P14 analytics)."""

    stage: str
    count: int
    total_value: int = 0
    avg_probability: float = 0.0


class EmailStat(BaseModel):
    """Stats e-mail par template (P14 analytics) + breakdown par statut."""

    template_id: str | None = None
    count: int
    by_status: dict[str, int] = {}


class SourceStat(BaseModel):
    """Stats contact par source (P14 analytics) + taux de conversion."""

    source: str
    count: int
    conversion_pct: float = 0.0  # % de contacts « convertis » (won/contacted/qualified)


class AnalyticsOverview(BaseModel):
    """Vue d'ensemble KPIs (P14) : CRM (contacts + opportunités) + e-mails + sync LinkedIn."""

    contacts_total: int
    contacts_by_source: dict[str, int] = {}
    contacts_by_status: dict[str, int] = {}
    opportunities_total: int
    opportunities_by_stage: dict[str, int] = {}
    pipeline_value: int = 0
    pipeline_currency: str = "EUR"
    emails_total: int
    emails_by_status: dict[str, int] = {}
    last_sync_at: datetime | None = None
    last_sync_status: str | None = None
    last_sync_imported: int = 0
    last_sync_total: int = 0
    sync_runs_total: int = 0


class AnalyticsPipeline(BaseModel):
    """Pipeline agrégé par stage (P14) + win rate + valeur totale."""

    stages: list[PipelineStageStat] = []
    win_rate_pct: float = 0.0  # closed_won / total
    total_value: int = 0
    total_opportunities: int = 0


class AnalyticsEmails(BaseModel):
    """Stats e-mail (P14) : par template + par statut + taux de succès (sent/(sent+failed))."""

    by_template: list[EmailStat] = []
    by_status: dict[str, int] = {}
    success_rate_pct: float = 0.0
    total: int = 0


class AnalyticsSources(BaseModel):
    """Stats contacts par source (P14) + conversion."""

    by_source: list[SourceStat] = []
    total: int = 0


# ---- Analytics v2 (P24) : funnel, cohorts, attribution ----
_FUNNEL_STAGES = ["prospecting", "qualified", "proposal", "negotiation", "closed_won"]


class FunnelStage(BaseModel):
    """Maillon d'un funnel de conversion (P24). count = opps actuellement à ce stage ;
    cumulative = opps ayant atteint AU MOINS ce stage (ce stage + tous les stages ultérieurs du
    chemin gagnant). conversion_from_prev_pct = cumulative[i]/cumulative[i-1]. drop_off = perdus
    entre le précédent et ce stage. closed_lost exclu du chemin gagnant (branche de perte)."""

    stage: str
    order: int
    count: int  # opps actuellement à ce stage
    cumulative: int  # opps à ce stage + stages ultérieurs (chemin gagnant)
    conversion_from_prev_pct: float = 0.0  # cumulative / cumulative précédent
    drop_off: int = 0  # cumulative précédent - cumulative


class AnalyticsFunnel(BaseModel):
    """Funnel de conversion pipeline (P24) : chemin prospecting→qualified→proposal→negotiation→
    closed_won avec taux de conversion entre maillons + conversion globale (won/entered)."""

    stages: list[FunnelStage] = []
    total_entered: int = 0  # cumulative prospecting (toutes opps non closed_lost)
    total_won: int = 0  # cumulative closed_won
    overall_conversion_pct: float = 0.0  # total_won / total_entered


class CohortRow(BaseModel):
    """Ligne de cohorte (P24) : opportunités groupées par mois d'acquisition (created_at tronqué au
    mois YYYY-MM). by_stage = répartition par stage actuel. win_rate_pct = closed_won/total.
    revenue_won = somme des values closed_won. avg_time_to_close_days = jours moyens created_at→
    closed_at pour les won (NULL si pas de closed_at)."""

    month: str  # YYYY-MM
    total_opps: int
    by_stage: dict[str, int] = {}
    closed_won_count: int = 0
    win_rate_pct: float = 0.0
    revenue_won: int = 0
    avg_time_to_close_days: float | None = None


class AnalyticsCohorts(BaseModel):
    """Cohortes par mois d'acquisition (P24) : matrice mois x stage + win rate + revenue par cohorte."""

    cohorts: list[CohortRow] = []
    total_opps: int = 0
    total_won: int = 0
    avg_win_rate_pct: float = 0.0


class AttributionSource(BaseModel):
    """Attribution revenue par source d'acquisition (P24). Jointure Opportunity→Contact.source.
    opps_count = nb d'opps issues de cette source. closed_won_count = won. revenue_won = somme
    values won. win_rate_pct = won/opps. avg_deal_size = revenue_won/won (0 si pas de won)."""

    source: str
    opps_count: int
    closed_won_count: int = 0
    revenue_won: int = 0
    win_rate_pct: float = 0.0
    avg_deal_size: float = 0.0


class AnalyticsAttribution(BaseModel):
    """Attribution revenue multi-source (P24) : quel canal (LinkedIn/manual/import) génère le plus
    de revenue won + win rate + deal size moyen."""

    by_source: list[AttributionSource] = []
    total_revenue_won: int = 0
    total_won: int = 0
    best_source: str | None = None  # source avec le plus de revenue_won


# ---- Séquences & automatisation (P15) ----
class SequenceStepCreate(BaseModel):
    """Step d'une séquence à la création (P15). template_id = template P13, delay_days = délai."""

    template_id: str  # cold_outreach | follow_up | intro | re_engagement
    delay_days: int = 0  # délai avant envoi après le step précédent (0 = immédiat)


class SequenceStepOut(BaseModel):
    """Step d'une séquence sérialisé (P15, étendu P27 multicanal). channel = email|linkedin|call|sms|
    manual. template_id nullable (steps non-email). channel_config = config spécifique au canal."""

    id: str
    step_order: int
    template_id: str | None = None  # P27 : nullable pour steps non-email
    delay_days: int
    channel: str = "email"  # P27 : canal (default email — backward compat P15)
    channel_config: dict | None = None  # P27 : config spécifique au canal
    model_config = ConfigDict(from_attributes=True)


class SequenceCreate(BaseModel):
    """Création d'une séquence + ses steps (P15)."""

    name: str
    description: str = ""
    status: str = "draft"  # draft | active | paused
    steps: list[SequenceStepCreate]


class SequenceUpdate(BaseModel):
    """Mise à jour partielle d'une séquence (P15). Au moins un champ."""

    name: str | None = None
    description: str | None = None
    status: str | None = None  # draft | active | paused


class SequenceOut(BaseModel):
    """Séquence sérialisée (P15) avec steps + count enrollments."""

    id: str
    tenant_id: str
    name: str
    description: str
    status: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    steps: list[SequenceStepOut] = []
    enrollments_count: int = 0


class EnrollRequest(BaseModel):
    """Enrollment d'un contact dans une séquence (P15)."""

    contact_id: str


class SequenceEnrollmentOut(BaseModel):
    """Enrollment sérialisé (P15)."""

    id: str
    sequence_id: str
    contact_id: str
    current_step_order: int
    status: str  # active | completed | paused | failed
    enrolled_by_user_id: str | None = None
    enrolled_at: datetime
    next_step_at: datetime
    completed_at: datetime | None = None
    error_detail: str | None = None


class SequenceProcessResult(BaseModel):
    """Résultat d'une passe du scheduler sur une séquence (P15)."""

    sequence_id: str
    processed: int
    sent: int
    skipped: int
    failed: int
    completed: int


class SequenceStats(BaseModel):
    """Stats d'une séquence (P15) : counts par statut d'enrollment."""

    sequence_id: str
    total_enrollments: int
    active: int
    completed: int
    paused: int
    failed: int


# ---- Batch enrollment (P16 — ciblage segment → séquence) ----
class EnrollBatchRequest(BaseModel):
    """Batch enrollment (P16) : segment_id (applique le criteria du segment) OU contact_ids explicites."""

    segment_id: str | None = None
    contact_ids: list[str] = []


class EnrollBatchResult(BaseModel):
    """Résultat d'un batch enrollment (P16) : enrolled + skipped (déjà active) + total."""

    enrolled: int
    skipped: int
    total: int


# ---- Webhooks outbound (P18) ----
class WebhookCreate(BaseModel):
    """Création d'un webhook (P18). url = endpoint POST cible. events = souscriptions
    (liste blanche SUPPORTED_EVENTS du service). secret auto-généré (32 hex) si absent."""

    url: str = Field(..., min_length=8, max_length=500)
    events: list[str] = Field(..., min_length=1)
    secret: str | None = None  # auto-généré si null
    active: bool = True


class WebhookUpdate(BaseModel):
    """Mise à jour partielle d'un webhook (P18). Tous champs optionnels."""

    url: str | None = Field(None, min_length=8, max_length=500)
    events: list[str] | None = None
    active: bool | None = None


class WebhookOut(BaseModel):
    """Webhook sérialisé (P18). secret jamais exposé en lecture (secrets non leakés)."""

    id: str
    url: str
    events: list[str]
    active: bool
    created_by: str | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class WebhookRotateResult(BaseModel):
    """Résultat de la rotation du secret webhook (P24-A). secret = nouveau plaintext affiché UNE
    SEULE FOIS — l'admin le copie chez le consumer ; non récupérable ultérieurement (chiffré au
    repos dans secret_enc). Le consumer doit reconfigurer sa vérif X-RevPulse-Signature."""

    webhook_id: str
    secret: str


class WebhookDeliveryOut(BaseModel):
    """Livraison d'un webhook (P18). payload = JSON envoyé (snap à l'émission)."""

    id: str
    webhook_id: str
    event: str
    payload: dict
    status: str
    response_code: int | None = None
    attempts: int
    max_attempts: int
    last_error: str | None = None
    created_at: datetime
    delivered_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)


class WebhookTestResult(BaseModel):
    """Résultat d'un test ping (P18) : crée + livre une delivery `webhook.test`."""

    delivery_id: str
    status: str
    response_code: int | None = None
    error: str | None = None


# ─── P20 : Webhooks inbound ─────────────────────────────────────────────────────


class InboundWebhookCreate(BaseModel):
    """Création d'un webhook inbound (P20). source_name = label du système source (ex. "Website
    form"). field_map = dict optionnel {payload_key: contact_field} pour mapper un payload
    non-standard ; None = default map (identity sur first_name/last_name/email/phone/title/
    linkedin_url). token auto-généré (32 hex) — retourné une fois en Out."""

    source_name: str = Field(..., min_length=1, max_length=100)
    field_map: dict[str, str] | None = None
    active: bool = True


class InboundWebhookUpdate(BaseModel):
    """Mise à jour partielle d'un webhook inbound (P20). token non modifiable (identifiant de
    route — rotation = delete + recreate). Tous champs optionnels."""

    source_name: str | None = Field(None, min_length=1, max_length=100)
    field_map: dict[str, str] | None = None
    active: bool | None = None


class InboundWebhookOut(BaseModel):
    """Webhook inbound sérialisé (P20). token EXPOSÉ (contrairement au secret HMAC outbound) —
    c'est l'identifiant public dans l'URL /api/inbound/webhook/{token}, l'admin le copie dans la
    config du système source."""

    id: str
    tenant_id: str
    token: str
    source_name: str
    field_map: dict[str, str] | None = None
    active: bool
    created_by: str | None = None
    created_at: datetime


class InboundResult(BaseModel):
    """Résultat d'une réception inbound (P20/P24-A) : action created/updated/duplicate (P24-A
    idempotence — event_id déjà traité), contact_id créé ou mis à jour (None si duplicate),
    matched_by = clé de dédup (email/linkedin_url/none/event_id)."""

    action: str  # created | updated | duplicate (P24-A)
    contact_id: str | None = None  # None si duplicate (P24-A)
    matched_by: str  # email | linkedin_url | none | event_id (P24-A)


# ─── P24-B — AI Gateway multi-provider ────────────────────────────────────────


class AIProviderConfigCreate(BaseModel):
    """Création d'une config provider IA (P24-B). provider dans SUPPORTED_PROVIDERS. api_key
    plaintext fourni par l'admin → chiffré Fernet à la persistence (jamais stocké en clair)."""
    provider: str = Field(..., description="openai|anthropic|gemini|deepseek|glm|openrouter|ollama")
    label: str = Field(..., min_length=1, max_length=80)
    api_key: str | None = None  # plaintext → chiffré à la persistence
    base_url: str | None = None
    enabled: bool = True
    priority: int = 0
    quota_tokens_month: int | None = None
    cost_usd_month: float | None = None


class AIProviderConfigUpdate(BaseModel):
    """Update partielle d'une config provider IA (P24-B). api_key si fourni = rotation de clé."""
    label: str | None = None
    api_key: str | None = None  # si fourni → re-chiffré (rotation)
    base_url: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    quota_tokens_month: int | None = None
    cost_usd_month: float | None = None


class AIProviderConfigOut(BaseModel):
    """Config provider sérialisée (P24-B). api_key JAMAIS exposé (chiffré au repos, decrypt seulement
    au moment de l'appel). tokens_used_month/cost_incurred_month = usage courant (reset mensuel)."""
    id: str
    provider: str
    label: str
    base_url: str | None = None
    enabled: bool
    priority: int
    quota_tokens_month: int | None = None
    cost_usd_month: float | None = None
    tokens_used_month: int
    cost_incurred_month: float
    last_used_at: datetime | None = None
    has_api_key: bool  # True si api_key_enc set (sans exposer la clé)
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PromptVersionCreate(BaseModel):
    """Création d'une version de prompt (P24-B). name + content obligatoires. version auto-incrémenté
    par (tenant, name). active=True → désactive les versions précédentes du même name."""
    name: str = Field(..., min_length=1, max_length=80)
    content: str = Field(..., min_length=1)
    variables: list[str] | None = None
    active: bool = True
    notes: str | None = None


class PromptVersionOut(BaseModel):
    """Version de prompt sérialisée (P24-B)."""
    id: str
    name: str
    version: int
    content: str
    variables: list[str] | None = None
    active: bool
    notes: str | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AIGenerateRequest(BaseModel):
    """Requête de génération IA (P24-B). prompt_name = PromptVersion actif à rendre avec variables.
    model = id API optionnel (sinon default du provider). dry_run=True → SimulatedProvider."""
    prompt_name: str = Field(..., min_length=1, max_length=80)
    variables: dict = Field(default_factory=dict)
    model: str | None = None
    max_tokens: int = Field(default=1000, ge=1, le=8000)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    system: str | None = None
    dry_run: bool = False


class AIGenerateResponse(BaseModel):
    """Réponse de génération IA (P24-B). simulated=True → provider simulé (jamais présenté comme réel).
    require_human_validation=True → injection détectée, l'action externe est bloquée jusqu'à validation."""
    generation_id: str | None = None
    text: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    simulated: bool
    require_human_validation: bool
    injection_reasons: list[str] = []
    pii_redacted_count: int = 0
    latency_ms: int
    error: str | None = None
    status: str


class AIGenerationOut(BaseModel):
    """Historique de génération (P24-B). input_text = prompt rendu APRÈS PII redact."""
    id: str
    provider: str
    model: str
    prompt_name: str | None = None
    output_text: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float
    latency_ms: int | None = None
    status: str
    simulated: bool
    error: str | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AIModelInfo(BaseModel):
    """Métadonnées d'un modèle IA (P24-B). configured=True si le tenant a une config provider active."""
    provider: str
    model: str
    label: str
    context_window: int
    cost_in_per_1k: float
    cost_out_per_1k: float
    configured: bool


# ─── P25 — Agents IA spécialisés + AI Copilot ─────────────────────────────────


class AgentInfo(BaseModel):
    """Métadonnées d'un agent IA (P25). name = id (qualification|scoring|research|...).
    description = ce que l'agent fait (pour l'affichage dans le Copilot / la liste)."""
    name: str
    description: str


class AgentRunRequest(BaseModel):
    """Requête d'exécution d'un agent (P25). params = inputs spécifiques à l'agent (contact_id,
    opportunity_id, icp_description, topic, competitor, template... selon l'agent)."""
    params: dict = Field(default_factory=dict)


class AgentRunResponse(BaseModel):
    """Réponse d'un run d'agent (P25). text = sortie générée. simulated=True → provider simulé
    (jamais présenté comme réel). require_human_validation=True → l'appelant DOIT bloquer l'action
    externe + demander validation humaine (injection OU action externe proposée)."""
    agent_run_id: str | None = None
    agent: str
    text: str
    simulated: bool
    require_human_validation: bool
    status: str
    error: str | None = None
    generation_id: str | None = None


class AgentRunOut(BaseModel):
    """Historique d'un run d'agent (P25). context = inputs (JSON). result_text = sortie."""
    id: str
    agent: str
    context: dict | None = None
    result_text: str | None = None
    status: str
    simulated: bool
    require_human_validation: bool
    error: str | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class CopilotChatRequest(BaseModel):
    """Requête de chat Copilot (P25). message = texte libre utilisateur. Le Copilot route vers le
    bon agent (keyword matching) + optionnellement params explicites (contact_id, etc.)."""
    message: str = Field(..., min_length=1, max_length=2000)
    params: dict = Field(default_factory=dict)


class CopilotChatResponse(BaseModel):
    """Réponse du Copilot (P25). routed_agent = agent sélectionné (None si aucun match → réponse
    générique listant les agents). result = sortie de l'agent. suggestions = prochaines actions
    suggérées (validation, exécution, autre agent)."""
    routed_agent: str | None = None
    text: str
    simulated: bool
    require_human_validation: bool
    agent_run_id: str | None = None
    status: str
    suggestions: list[str] = []
    error: str | None = None


# ─── P26 — LinkedIn Content Studio professionnel ─────────────────────────────


class ContentTemplateRequest(BaseModel):
    """Création d'un modèle de post (P26). category = storytelling|insight|announcement|carousel|
    question|bts. structure = template avec {{variables}}."""
    name: str = Field(..., min_length=1, max_length=120)
    category: str = Field(..., max_length=40)
    structure: str = Field(..., min_length=1)
    variables: list[str] = Field(default_factory=list)
    style: str | None = None


class ContentTemplateOut(BaseModel):
    """Modèle de post (P26). is_system=True → immuable (seedé, non modifiable/supprimable)."""
    id: str
    name: str
    category: str
    structure: str
    variables: list[str] | None = None
    style: str | None = None
    is_system: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ContentGenerateRequest(BaseModel):
    """Génération de post via LinkedInAgent P25 (P26). topic + style + title. La génération utilise
    l'AI Gateway P24-B (fallback SimulatedProvider si no provider configured)."""
    topic: str = Field(..., min_length=1, max_length=200)
    style: str = Field(default="expert, terrain, data-driven", max_length=100)
    title: str | None = None


class ContentCarouselRequest(BaseModel):
    """Génération d'un carrousel (P26). topic + slides_count (2-10) + style. Retourne les slides
    (texte généré, image_url=NULL — l'utilisateur attache ses visuels via le Content Studio UI)."""
    topic: str = Field(..., min_length=1, max_length=200)
    slides_count: int = Field(default=5, ge=2, le=10)
    style: str = Field(default="éducatif", max_length=100)


class ContentPostRequest(BaseModel):
    """Création manuelle d'un post (P26). content_text requis. status par défaut draft."""
    title: str | None = None
    content_text: str = Field(..., min_length=1)
    topic: str | None = None
    style: str | None = None
    hashtags: str | None = None
    template_id: str | None = None


class ContentPostUpdate(BaseModel):
    """Mise à jour d'un post (P26). Tous les champs optionnels (partial update)."""
    title: str | None = None
    content_text: str | None = None
    topic: str | None = None
    style: str | None = None
    hashtags: str | None = None
    carousel: list[dict] | None = None
    media_urls: list[str] | None = None
    template_id: str | None = None


class ContentScheduleRequest(BaseModel):
    """Planification d'un post (P26). scheduled_at (ISO datetime, futur)."""
    scheduled_at: datetime


class ContentRejectRequest(BaseModel):
    """Rejet d'un post (P26). reason obligatoire (validation collaborative)."""
    reason: str = Field(..., min_length=1, max_length=1000)


class ContentPostOut(BaseModel):
    """Post Content Studio (P26). status workflow : draft|pending_review|approved|rejected|scheduled|
    published. ai_generation_id = audit trail (P24-B → P25 → P26). linkedin_urn = URN LinkedIn (simulé
    en v1). stats = {impressions, likes, comments, shares} (NULL jusqu'à sync LinkedIn API réelle)."""
    id: str
    title: str | None = None
    content_text: str
    topic: str | None = None
    style: str | None = None
    hashtags: str | None = None
    carousel: list[dict] | None = None
    media_urls: list[str] | None = None
    template_id: str | None = None
    status: str
    scheduled_at: datetime | None = None
    published_at: datetime | None = None
    linkedin_urn: str | None = None
    ai_generation_id: str | None = None
    validation_requested_by: str | None = None
    validation_requested_at: datetime | None = None
    validated_by: str | None = None
    validated_at: datetime | None = None
    rejected_reason: str | None = None
    linkedin_account_id: str | None = None
    stats: dict | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ContentCalendarItem(BaseModel):
    """Item du calendrier éditorial (P26). date (YYYY-MM-DD) + posts planifiés/published ce jour."""
    date: str
    posts: list[ContentPostOut] = []


class ContentStats(BaseModel):
    """Statistiques Content Studio (P26). posts_by_status = {draft: N, pending_review: N, ...}.
    total_published + total_scheduled + total_engagement (somme des stats published)."""
    total_posts: int
    posts_by_status: dict[str, int]
    total_published: int
    total_scheduled: int
    total_templates: int
    total_engagement: int


# ─── P27 — Prospection avancée (multicanal + A/B testing + intent) ───────────


class MultichannelStepRequest(BaseModel):
    """Création d'un step multicanal (P27). channel = email|linkedin|call|sms|manual. template_id =
    template e-mail (requis si email, NULL sinon). channel_config = config spécifique au canal
    (linkedin_action, call_script, manual_task, sms_text)."""
    step_order: int = Field(..., ge=1)
    channel: str = Field(..., max_length=20)
    template_id: str | None = None
    delay_days: int = Field(default=0, ge=0)
    channel_config: dict | None = None


class SequenceStepVariantRequest(BaseModel):
    """Création d'une variante A/B (P27). variant = 'A' | 'B'. subject_override + body_override =
    contenu alternatif (si NULL → utilise le template_id du step)."""
    variant: str = Field(..., max_length=5)  # A | B
    label: str | None = None
    subject_override: str | None = None
    body_override: str | None = None


class SequenceStepVariantOut(BaseModel):
    """Variante A/B (P27). sent_count = dénominateur pour les taux (open_rate = open_count/sent_count)."""
    id: str
    step_id: str
    variant: str
    label: str | None = None
    subject_override: str | None = None
    body_override: str | None = None
    sent_count: int
    open_count: int
    click_count: int
    reply_count: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class VariantMetricsRequest(BaseModel):
    """Enregistrement d'une métrique sur une variante (P27 A/B testing). event = sent|open|click|reply.
    count = incrément (default 1)."""
    event: str = Field(..., max_length=10)  # sent|open|click|reply
    count: int = Field(default=1, ge=1)


class ABTestResult(BaseModel):
    """Résultat A/B d'une variante (P27). taux = pourcentage (open_rate = open_count/sent_count*100)."""
    variant_id: str
    variant: str
    label: str | None = None
    sent_count: int
    open_count: int
    click_count: int
    reply_count: int
    open_rate: float
    click_rate: float
    reply_rate: float


class IntentSignalRequest(BaseModel):
    """Ajout d'un signal d'intention (P27). signal_type = job_change|funding|hire_spike|news|engagement|
    website_visit. score 1-100 (clamp). source = manual|webhook|enrichment."""
    signal_type: str = Field(..., max_length=30)
    description: str | None = None
    score: int = Field(default=10, ge=1, le=100)
    source: str = Field(default="manual", max_length=20)


class IntentSignalOut(BaseModel):
    """Signal d'intention (P27). detected_at = quand le signal a été détecté (fenêtre 90j pour
    intent_score). created_by = acteur (NULL si source=webhook)."""
    id: str
    contact_id: str
    signal_type: str
    description: str | None = None
    score: int
    source: str
    detected_at: datetime
    created_by: str | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class IntentLeader(BaseModel):
    """Contact leaderboard par intent_score (P27). intent_score = somme des IntentSignal.score récents
    (90j, cap 100)."""
    id: str
    first_name: str
    last_name: str
    email: str
    company: str | None = None
    title: str | None = None
    intent_score: int
    signal_count: int


class EnrichmentResult(BaseModel):
    """Résultat d'enrichissement (P27 v1 — SIMULÉ). simulated=True (anti-fake). data = champs
    enrichis (title_confidence, company_employee_count, etc.). v2 branchera un vrai provider."""
    contact_id: str
    enriched_at: str | None = None
    simulated: bool
    data: dict


# ─── P28 — Warmup inbox + Sales Intelligence ─────────────────────────────────


class InboxWarmupRequest(BaseModel):
    """Création d'une config warmup inbox (P28). sender_email = adresse à warmer. target_volume =
    volume quotidien cible (10-1000, clamp). ramp_days = durée de la rampe (7-90j, clamp)."""
    sender_email: str = Field(..., max_length=255)
    target_volume: int = Field(default=100, ge=10, le=1000)
    ramp_days: int = Field(default=30, ge=7, le=90)


class InboxWarmupOut(BaseModel):
    """Config warmup (P28). ramp_status : pending|ramping|active|paused|completed. current_day = jour
    actuel (0 = pas démarré). daily_volume = volume calculé (rampe linéaire)."""
    id: str
    sender_email: str
    target_volume: int
    ramp_days: int
    current_day: int
    daily_volume: int
    ramp_status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class WarmupStatsOut(BaseModel):
    """Stats warmup (P28). progress_pct = current_day/ramp_days*100. open_rate/reply_rate sur sent_count."""
    warmup_id: str
    sender_email: str
    target_volume: int
    ramp_days: int
    current_day: int
    daily_volume: int
    ramp_status: str
    progress_pct: float
    sent_count: int
    open_count: int
    reply_count: int
    open_rate: float
    reply_rate: float


class ContactFunnelStage(BaseModel):
    """Stage du contacts-funnel (P28). status = new|qualified|contacted|nurture|won. Distinct du
    FunnelStage P24 (pipeline-funnel par opportunity stage avec cumulative/drop_off)."""
    status: str
    count: int


class FunnelOut(BaseModel):
    """Contacts-funnel de conversion (P28). stages = funnel ordonné (new → won). lost = à part.
    conversion_rate = won / total * 100. Distinct du AnalyticsFunnel P24 (pipeline opportunities)."""
    stages: list[ContactFunnelStage]
    lost: int
    total: int
    conversion_rate: float


class CohortOut(BaseModel):
    """Cohorte d'enrollment (P28). cohort = semaine ISO (YYYY-Www). completion_rate = completed/total*100."""
    cohort: str
    total: int
    completed: int
    active: int
    paused: int
    failed: int
    completion_rate: float


class ForecastStage(BaseModel):
    """Stage du forecast pipeline (P28). raw_value = sum(value). weighted_value = sum(value * prob/100)."""
    stage: str
    count: int
    raw_value: int
    weighted_value: int


class ForecastOut(BaseModel):
    """Prévisions pipeline (P28). weighted_forecast = projection statistique (pondérée par probability).
    raw_pipeline = somme brute des opportunities ouvertes. Pas une promesse de revenu."""
    stages: list[ForecastStage]
    total_opportunities: int
    raw_pipeline: int
    weighted_forecast: int
    currency: str


# ─── P34 — Career : référentiel métiers + CV (espace candidat isolé) ─────────


class CareerProfileRequest(BaseModel):
    """Upsert profil Career (P34). target_job_title = métier visé. contract_types = CDI|CDD|freelance.
    salary_min/max (annuel brut) + tjm_min (freelance/jour). mobility = accepte relocalisation."""
    target_job_title: str | None = None
    preferred_location: str | None = None
    mobility: bool = False
    contract_types: list[str] | None = None
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    tjm_min: int | None = Field(default=None, ge=0)
    currency: str = "EUR"
    languages: list[str] | None = None
    # P40 — préférences notification (opt-in anti-spam, optionnelles sur l'upsert profil)
    alert_enabled: bool | None = None
    alert_min_score: int | None = Field(default=None, ge=0, le=100)
    alert_channels: list[str] | None = None  # ["in_app","email"]
    alert_frequency: str | None = None  # daily|weekly|instant

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, v: str) -> str:
        """P30 : valide la devise (EUR|USD|GNF|XOF). Accepte minuscules + normalise en majuscules."""
        if not v:
            return "EUR"
        code = v.upper().strip()
        if code not in ("EUR", "USD", "GNF", "XOF"):
            raise ValueError(f"Devise non supportée : {v}. Supportées : EUR, USD, GNF, XOF.")
        return code


class CareerProfileOut(BaseModel):
    """Profil Career (P34). user_id unique — isolation stricte (pas de tenant_id, accessible que par le user)."""
    id: str
    user_id: str
    target_job_title: str | None = None
    preferred_location: str | None = None
    mobility: bool
    contract_types: list[str] | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    tjm_min: int | None = None
    currency: str
    languages: list[str] | None = None
    # P40 — préférences notification (opt-in anti-spam)
    alert_enabled: bool = True
    alert_min_score: int = 70
    alert_channels: list[str] | None = None
    alert_frequency: str = "daily"
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class CVTextImport(BaseModel):
    """Import CV depuis texte brut (P34). title = libellé affiché. text = contenu. locale = fr|en."""
    title: str = Field(..., max_length=200)
    text: str = Field(..., min_length=10, max_length=50000)
    locale: str = Field(default="fr", max_length=5)


class CVOut(BaseModel):
    """CV importé (P34). source_type = text|pdf|docx. parsed_data = {skills, experiences, certifications,
    education, heuristic}. version = auto-incrément par profile. is_active = 1 seul actif."""
    id: str
    career_profile_id: str
    title: str
    source_type: str
    source_filename: str | None = None
    locale: str
    version: int
    is_active: bool
    parsed_data: dict | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class JobReferenceOut(BaseModel):
    """Métier du référentiel (P34). code = ROME/ESCO/O*NET. source = rome|esco|onet. category = domaine."""
    id: str
    code: str
    source: str
    label: str
    category: str
    locale: str
    model_config = ConfigDict(from_attributes=True)


class TransferrableSkillsOut(BaseModel):
    """Compétences transférables détectées (P34). Soft skills cross-domain — utiles pour un changement
    de métier (P36 matching). Détectées via detect_transferrable_skills (heuristic v1)."""
    cv_id: str
    transferrable_skills: list[str]


# ─── P35 — Career : recherche d'opportunités (connecteurs + postings + saved searches) ──────


class SearchCriteriaRequest(BaseModel):
    """Critères de recherche multicritère (P35). keywords = texte libre. source_connectors = liste
    des connecteurs à interroger (None = tous configurés)."""
    keywords: str | None = None
    location: str | None = None
    contract_types: list[str] | None = None
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    radius_km: int | None = Field(default=None, ge=0, le=500)
    source_connectors: list[str] | None = None  # None = tous


class JobPostingOut(BaseModel):
    """Offre d'emploi (P35). source_connector = linkedin_jobs|freework|malt|... (12 providers)."""
    id: str
    title: str
    company: str | None = None
    location: str | None = None
    url: str | None = None
    description: str | None = None
    contract_type: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    source_connector: str
    is_expired: bool
    fetched_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ConnectorStatusOut(BaseModel):
    """Statut d'un connecteur après search (P35). status = ok|not_configured|error|unauthorized."""
    name: str
    status: str
    count: int
    error: str | None = None
    configured: bool


class SearchResponse(BaseModel):
    """Réponse de recherche (P35). postings = offres actives filtrées. connector_statuses = statut par
    connecteur (transparent — un connecteur not_configured est visible, JAMAIS masqué)."""
    postings: list[JobPostingOut]
    connector_statuses: list[ConnectorStatusOut]
    total: int
    expired_marked: int


class ConnectorInfoOut(BaseModel):
    """Info d'un connecteur (P35) — pour la documentation/UI."""
    name: str
    auth_method: str
    base_url: str
    configured: bool
    credential_env: str | None = None


class SavedSearchRequest(BaseModel):
    """Création/update d'une recherche sauvegardée (P35). criteria = critères multicritères.
    alert_enabled + alert_frequency (daily|weekly) → v2 scheduler alertes P40."""
    name: str = Field(..., max_length=200)
    criteria: SearchCriteriaRequest
    alert_enabled: bool = False
    alert_frequency: str = Field(default="daily", pattern="^(daily|weekly)$")


class SavedSearchOut(BaseModel):
    """Recherche sauvegardée (P35)."""
    id: str
    career_profile_id: str
    name: str
    criteria: dict
    alert_enabled: bool
    alert_frequency: str
    last_run_at: datetime | None = None
    last_run_postings_count: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ─── P36 — Career : matching intelligent CV↔offre (score hybride explicable) ──


class MatchingCriterionOut(BaseModel):
    """Sous-score d'un critère de matching (P36). score 0-100, weight 0-1, contribution = weight*score.
    eliminatory=True si critère obligatoire échoué (cap le total à 60)."""
    name: str  # skills|experience|certifications|location|salary|languages
    weight: float
    score: float
    detail: str
    eliminatory: bool


class MatchingBreakdownOut(BaseModel):
    """Résultat du matching CV↔offre (P36). total_score = somme pondérée (0-100, capée à 60 si
    éliminatoire). missing_skills = compétences requises non présentes dans le CV. disclaimer :
    score = compatibilité, NE PAS présenter comme probabilité de recrutement (anti-sur-déclaration)."""
    cv_id: str
    posting_id: str
    total_score: float
    criteria: list[MatchingCriterionOut]
    missing_skills: list[str]
    eliminatory_flags: list[str]
    disclaimer: str


class MatchingRankedItemOut(BaseModel):
    """Item d'une recherche matching ranked (P36) — match du CV actif contre N postings."""
    posting: JobPostingOut
    total_score: float
    missing_skills: list[str]
    eliminatory_flags: list[str]


class MatchingSearchRequest(BaseModel):
    """Requête de matching ranked (P36). cv_id (si omis → CV actif du profil). limit = nb postings à scorer
    (cap 50 anti-abus). filters optionnels (keywords/location/source)."""
    cv_id: str | None = None  # None → CV is_active=True du profil
    keywords: str | None = None
    location: str | None = None
    source: str | None = None
    limit: int = Field(default=20, ge=1, le=50)


# ─── P37 — Career : CV adaptatif (versions ciblées par offre, anti-invention) ──


class AdaptCVRequest(BaseModel):
    """Requête d'adaptation de CV (P37). posting_id = offre cible (si adaptation pour une offre spécifique).
    Si posting_id omis : target_job_title + target_company (critères libres). Le CV source est adapté
    via cv_adapter.adapt_cv (réordonne skills par pertinence + sélectionne expériences + summary ciblé).
    Invariant P37 : n'ajoute QUE ce qui est dans le CV source (anti-invention vérifié runtime)."""
    posting_id: str | None = None  # None → critères libres (target_job_title requis)
    target_job_title: str | None = Field(default=None, max_length=200)
    target_company: str | None = Field(default=None, max_length=200)


class AdaptedCVOut(BaseModel):
    """CV adapté (P37). adapted_data = structure adaptée (targeted_title/summary/reordered_skills/
    selected_experiences/matched_certifications/missing_skills/match_score). match_score = score P36."""
    id: str
    cv_source_id: str
    career_profile_id: str
    posting_id: str | None = None
    target_job_title: str | None = None
    target_company: str | None = None
    adapted_data: dict
    match_score: float | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ─── P38 — Career : Candidatures + Gmail/Outlook (pipeline de candidatures) ────


class ApplicationCreateRequest(BaseModel):
    """Création d'une candidature (P38). posting_id (offre cible, optional), adapted_cv_id (CV adapté,
    optional). company + job_title requis (si pas de posting_id). recipient_email + application_method=email
    pour envoi différé via POST /applications/{id}/send. generate_cover_letter=True → génère la lettre
    automatiquement depuis le CV source (anti-invention P37/P38 invariant)."""
    posting_id: str | None = None
    adapted_cv_id: str | None = None
    cv_source_id: str | None = None  # requis si generate_cover_letter=True (CV source pour la lettre)
    company: str = Field(..., min_length=1, max_length=200)
    job_title: str = Field(..., min_length=1, max_length=200)
    recipient_email: str | None = Field(default=None, max_length=254)
    application_method: str = Field(default="manual", pattern="^(manual|email|external)$")
    notes: str | None = Field(default=None, max_length=2000)
    generate_cover_letter: bool = False  # True → cover_letter.generate_cover_letter(cv, posting, profile)


class ApplicationUpdateRequest(BaseModel):
    """Mise à jour d'une candidature (P38) — tracking pipeline. status (submitted/interview/offer/rejected/
    withdrawn), notes optionnelles. Chaque changement de status ajoute une entrée au timeline."""
    status: str | None = Field(default=None, pattern="^(submitted|interview|offer|rejected|withdrawn)$")
    notes: str | None = Field(default=None, max_length=2000)


class JobApplicationOut(BaseModel):
    """Candidature (P38). cover_letter = lettre générée (anti-invention). timeline = historique des étapes.
    sent_at = date d'envoi effectif (si email_sender). Anti-spam : envoi explicite via POST /send."""
    id: str
    career_profile_id: str
    posting_id: str | None = None
    adapted_cv_id: str | None = None
    company: str
    job_title: str
    status: str
    cover_letter: str | None = None
    application_method: str
    recipient_email: str | None = None
    sent_at: datetime | None = None
    timeline: list | None = None
    notes: str | None = None
    applied_at: datetime
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ─── P40 — Notifications intelligentes ────────────────────────────────────────
class CareerNotificationOut(BaseModel):
    """Notification Career (P40). type : new_match|application_status|interview_reminder.
    channel : email|in_app. read_at null = non lu. payload JSON (posting_id/application_id/score/message).
    Isolée du Business Notification (tenant_id) — user_id scoped."""
    id: str
    user_id: str
    career_profile_id: str
    type: str
    channel: str
    title: str
    body: str
    payload: dict | None = None
    read_at: datetime | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class NotificationPrefsOut(BaseModel):
    """Préférences notification Career (P40). alert_enabled = opt-in explicite (False → aucune notif,
    anti-spam invariant). alert_min_score = seuil P36 déclenchant new_match (0-100)."""
    alert_enabled: bool
    alert_min_score: int
    alert_channels: list[str] | None = None
    alert_frequency: str
    model_config = ConfigDict(from_attributes=True)


class NotificationPrefsUpdate(BaseModel):
    """Mise à jour préférences notification (P40). Tous optionnels — opt-in explicite anti-spam."""
    alert_enabled: bool | None = None
    alert_min_score: int | None = Field(default=None, ge=0, le=100)
    alert_channels: list[str] | None = None  # sous-ensemble de ["in_app","email"]
    alert_frequency: str | None = Field(default=None, pattern="^(daily|weekly|instant)$")


# ─── P41 — Coach IA entretiens ────────────────────────────────────────────────
class InterviewStartRequest(BaseModel):
    """Démarre une session de simulation d'entretien (P41). target_job_title requis (métier visé).
    session_type : tech (questions techniques + 1 négo) | rhmixed (tech + RH + 1 négo)."""
    target_job_title: str = Field(..., max_length=200)
    category: str | None = None  # data|product|finance|cyber|rh|generic (auto-détecté si null)
    session_type: str = Field(default="rhmixed", pattern="^(tech|rhmixed)$")
    application_id: str | None = None  # lien optionnel à une candidature


class AnswerRequest(BaseModel):
    """Soumet une réponse à une question de la session (P41). question_id requis + answer_text."""
    question_id: str = Field(..., max_length=50)
    answer_text: str = Field(..., min_length=5, max_length=5000)


class InterviewSessionOut(BaseModel):
    """Session d'entretien (P41). questions/answers/feedback JSON. score 0-100 (null si in_progress).
    Anti-invention : feedback factuel (keywords + missing skills cross-référence CV)."""
    id: str
    career_profile_id: str
    application_id: str | None = None
    target_job_title: str
    category: str | None = None
    session_type: str
    status: str
    questions: list | None = None
    answers: list | None = None
    feedback: dict | None = None
    score: int | None = None
    created_at: datetime
    completed_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)


# ─── P42 — Agent autonome Career ──────────────────────────────────────────────
class CareerAgentOut(BaseModel):
    """Agent Career (P42). auto_apply=False par défaut → prépare sans envoyer. consent_given requis
    pour envoi auto. status : idle|running|stopped (bouton arrêt immédiat). max_applications_per_day cap."""
    id: str
    career_profile_id: str
    enabled: bool
    auto_apply: bool
    consent_given: bool
    max_applications_per_day: int
    daily_sent_count: int
    last_run_at: datetime | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class CareerAgentConfigUpdate(BaseModel):
    """Mise à jour config agent (P42). enabled + auto_apply + consent_given (consent explicite).
    max_applications_per_day cap anti-abus (1-20)."""
    enabled: bool | None = None
    auto_apply: bool | None = None
    consent_given: bool | None = None
    max_applications_per_day: int | None = Field(default=None, ge=1, le=20)


class CareerAgentRunOut(BaseModel):
    """Run de l'agent (P42). steps JSON [{step, status, count, detail}] (audit trail). summary JSON
    (rapport quotidien agrégé). status : running|success|stopped|failed. Anti-invention : factuel."""
    id: str
    career_profile_id: str
    agent_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: str
    steps: list | None = None
    summary: dict | None = None
    applications_prepared: int
    applications_sent: int
    model_config = ConfigDict(from_attributes=True)


# ─── P46 : Admin SSO Enterprise OIDC (CRUD + lookup by email) ─────────────
class EnterpriseSSOOut(BaseModel):
    """Réponse admin : config SSO d'un tenant. client_secret JAMAIS retourné (has_secret seulement)."""
    tenant_id: str
    provider_type: str
    client_id: str
    has_secret: bool  # True si un client_secret est stocké (jamais la valeur)
    issuer_url: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scopes: str
    domains: list[str]  # domaines e-mail autorisés pour JIT provisioning
    enabled: bool
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class EnterpriseSSOConfig(BaseModel):
    """Body PUT /api/tenants/{tid}/sso : upsert config SSO OIDC. client_secret en clair (chiffré Fernet à la sauvegarde).
    Au moins 1 domaine requis (JIT gate — deny par défaut si domains vide)."""
    client_id: str
    client_secret: str  # plain → encrypt_token à la sauvegarde
    issuer_url: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scopes: str = "openid profile email"
    domains: list[str]  # ["corp.io", "revpulse.io"]
    enabled: bool = True


class SSOLookupRequest(BaseModel):
    """Body POST /api/auth/sso/lookup : email → trouve le tenant SSO par domaine."""
    email: str


class SSOLookupResponse(BaseModel):
    """Réponse lookup : tenant SSO + URL initiate (le frontend redirect vers cette URL)."""
    tenant_id: str
    tenant_name: str
    initiate_url: str  # /api/auth/sso/{tenant_id}/initiate
