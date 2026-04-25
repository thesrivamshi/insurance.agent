import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from chatkit.server import ChatKitServer
from chatkit.types import (
    AssistantMessageContent,
    AssistantMessageItem,
    ErrorEvent,
    ThreadItem,
    ThreadItemDoneEvent,
    ThreadMetadata,
    ThreadStreamEvent,
    UserMessageItem,
    UserMessageTextContent,
)

from app.agent import run_health_insurance_agent
from app.chatkit_store import ChatKitRequestContext, SQLAlchemyChatKitStore
from app.database import AsyncSessionLocal
from app.repository import create_lead
from app.safety import (
    SENSITIVE_RESPONSE,
    detect_sensitive_input,
    has_contact_details,
    sanitize_lead_fields,
)
from app.schemas import ChatAgentOutput, ChatMessage, Intent, LeadFields

logger = logging.getLogger("insurance_chatbot.chatkit")


class HealthInsuranceChatKitServer(ChatKitServer[ChatKitRequestContext]):
    def __init__(self) -> None:
        super().__init__(SQLAlchemyChatKitStore())

    async def respond(
        self,
        thread: ThreadMetadata,
        input_user_message: UserMessageItem | None,
        context: ChatKitRequestContext,
    ) -> AsyncIterator[ThreadStreamEvent]:
        if input_user_message is None:
            yield ErrorEvent(message="No user message was supplied.", allow_retry=False)
            return

        user_text = _extract_user_text(input_user_message)
        if not user_text:
            yield ErrorEvent(message="Please enter a message.", allow_retry=False)
            return

        if not thread.title:
            thread.title = _thread_title(user_text)
            await self.store.save_thread(thread, context=context)

        safety = detect_sensitive_input(user_text)
        if safety.blocked:
            yield self._assistant_message(thread, SENSITIVE_RESPONSE, context)
            return

        history = await self._load_chat_history(thread.id, input_user_message.id, context)

        try:
            agent_output = await run_health_insurance_agent(
                message=user_text,
                history=history,
                conversation_id=thread.id,
            )
            await _save_lead_if_needed(thread.id, agent_output)
            yield self._assistant_message(thread, agent_output.answer, context)
        except Exception:
            logger.exception("chatkit_agent_run_failed thread_id=%s", thread.id)
            yield ErrorEvent(
                message="I could not complete that request. Please try again.",
                allow_retry=True,
            )

    async def _load_chat_history(
        self,
        thread_id: str,
        current_user_item_id: str,
        context: ChatKitRequestContext,
    ) -> list[ChatMessage]:
        page = await self.store.load_thread_items(
            thread_id=thread_id,
            after=None,
            limit=40,
            order="asc",
            context=context,
        )
        messages: list[ChatMessage] = []
        for item in page.data:
            if item.id == current_user_item_id:
                continue
            message = _thread_item_to_chat_message(item)
            if message:
                messages.append(message)
        return messages[-20:]

    def _assistant_message(
        self,
        thread: ThreadMetadata,
        text: str,
        context: ChatKitRequestContext,
    ) -> ThreadItemDoneEvent:
        return ThreadItemDoneEvent(
            item=AssistantMessageItem(
                id=self.store.generate_item_id(
                    "message",
                    thread,
                    context,
                ),
                thread_id=thread.id,
                created_at=datetime.now(UTC),
                content=[AssistantMessageContent(text=text)],
            )
        )


def _extract_user_text(item: UserMessageItem) -> str:
    parts: list[str] = []
    for content in item.content:
        if isinstance(content, UserMessageTextContent):
            parts.append(content.text)
        else:
            parts.append(f"@{content.text}")
    return "".join(parts).strip()


def _thread_item_to_chat_message(item: ThreadItem) -> ChatMessage | None:
    if isinstance(item, UserMessageItem):
        content = _extract_user_text(item)
        return ChatMessage(role="user", content=content) if content else None
    if isinstance(item, AssistantMessageItem):
        content = "\n".join(part.text for part in item.content if part.text).strip()
        return ChatMessage(role="assistant", content=content) if content else None
    return None


def _thread_title(text: str) -> str:
    title = " ".join(text.split())
    return title[:57] + "..." if len(title) > 60 else title


async def _save_lead_if_needed(
    conversation_id: str,
    agent_output: ChatAgentOutput,
) -> None:
    fields = sanitize_lead_fields(agent_output.lead_fields)
    if not _should_save_lead(agent_output, fields):
        return

    async with AsyncSessionLocal() as db:
        await create_lead(
            db,
            fields=fields,
            source="chatkit",
            intent=agent_output.intent.value,
            conversation_id=conversation_id,
        )


def _should_save_lead(agent_output: ChatAgentOutput, fields: LeadFields) -> bool:
    return (
        agent_output.should_create_lead
        and has_contact_details(fields)
        and agent_output.intent not in {Intent.unrelated, Intent.unsafe_sensitive}
    )


chatkit_server = HealthInsuranceChatKitServer()
