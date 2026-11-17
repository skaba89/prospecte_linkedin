"""Schémas Pydantic (validation + sérialisation)."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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


class PlanUpdate(BaseModel):
    plan: str


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
    created_at: datetime | None = None


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
    """Step d'une séquence sérialisé (P15)."""

    id: str
    step_order: int
    template_id: str
    delay_days: int


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
