from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Lead
from app.schemas import LeadCreate, LeadFields
from app.safety import redact_sensitive_text, sanitize_lead_fields


async def create_lead(
    db: AsyncSession,
    *,
    fields: LeadFields,
    source: str,
    intent: str | None = None,
    conversation_id: str | None = None,
    notes: str | None = None,
) -> Lead:
    safe_fields = sanitize_lead_fields(fields)
    lead = Lead(
        **safe_fields.model_dump(),
        source=source,
        intent=intent,
        conversation_id=conversation_id,
        notes=redact_sensitive_text(notes),
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return lead


async def create_manual_lead(db: AsyncSession, payload: LeadCreate) -> Lead:
    field_names = set(LeadFields.model_fields)
    fields = LeadFields.model_validate(payload.model_dump(include=field_names))
    return await create_lead(
        db,
        fields=fields,
        source=payload.source,
        intent=payload.intent.value if payload.intent else None,
        conversation_id=payload.conversation_id,
        notes=payload.notes,
    )
