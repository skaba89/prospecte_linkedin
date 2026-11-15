"""Schémas Pydantic (validation + sérialisation)."""
from datetime import datetime
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
