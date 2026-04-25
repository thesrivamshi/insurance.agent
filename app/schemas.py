from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Intent(str, Enum):
    plan_question = "plan_question"
    compare_plans = "compare_plans"
    quote_request = "quote_request"
    callback_request = "callback_request"
    claim_question = "claim_question"
    unrelated = "unrelated"
    unsafe_sensitive = "unsafe_sensitive"


class LeadFields(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=320)
    city: str | None = Field(default=None, max_length=120)
    age: int | None = Field(default=None, ge=0, le=120)
    family_members: int | None = Field(default=None, ge=1, le=30)
    preferred_plan: str | None = Field(default=None, max_length=200)
    preferred_insurer: str | None = Field(default=None, max_length=200)
    budget_range: str | None = Field(default=None, max_length=120)
    has_existing_policy: bool | None = None
    pre_existing_condition: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def blank_strings_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ChatAgentOutput(BaseModel):
    answer: str = Field(
        description="User-facing answer. It must not guarantee premium, claims, eligibility, or coverage."
    )
    intent: Intent
    lead_fields: LeadFields = Field(default_factory=LeadFields)
    should_create_lead: bool = Field(
        default=False,
        description="True only when the user asked for a quote/callback/contact and supplied contact details.",
    )
    missing_lead_fields: list[str] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list)
    needs_human_callback: bool = False

    model_config = ConfigDict(extra="forbid")


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: Annotated[str, Field(min_length=1, max_length=5000)]

    model_config = ConfigDict(extra="forbid")


class ChatRequest(BaseModel):
    message: Annotated[str, Field(min_length=1, max_length=5000)]
    conversation_id: str | None = Field(default=None, max_length=128)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)

    model_config = ConfigDict(extra="forbid")


class LeadCreate(LeadFields):
    source: str = Field(default="manual", max_length=50)
    conversation_id: str | None = Field(default=None, max_length=128)
    intent: Intent | None = None
    notes: str | None = Field(default=None, max_length=2000)


class LeadRead(LeadFields):
    id: int
    source: str
    conversation_id: str | None = None
    intent: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LeadStatus(BaseModel):
    saved: bool
    id: int | None = None
    missing_fields: list[str] = Field(default_factory=list)
    reason: str | None = None


class SafetyStatus(BaseModel):
    blocked: bool = False
    warnings: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    classification: Intent
    extracted_fields: LeadFields
    lead: LeadStatus
    safety: SafetyStatus = Field(default_factory=SafetyStatus)
    needs_human_callback: bool = False


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]
    openai_configured: bool
    vector_store_configured: bool
