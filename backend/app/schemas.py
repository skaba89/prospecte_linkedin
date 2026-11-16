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
    """Résultat d'un envoi e-mail (P13) : la notification créée + statut d'envoi."""
    notification_id: str
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


class TaskUpdate(BaseModel):
    contact_id: str | None = None
    opportunity_id: str | None = None
    title: str | None = None
    due_date: date | None = None
    status: str | None = None
    priority: str | None = None
    assignee_id: str | None = None


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


class PipelineColumn(BaseModel):
    """Colonne Kanban : stage + opportunités + total valeur."""
    stage: str
    label: str
    color: str
    opportunities: list[OpportunityOut]
    count: int
    total_value: int
