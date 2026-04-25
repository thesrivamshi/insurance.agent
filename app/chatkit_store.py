from dataclasses import dataclass

from chatkit.store import NotFoundError, Store
from chatkit.types import Attachment, Page, ThreadItem, ThreadMetadata
from pydantic import TypeAdapter
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import ChatKitAttachment, ChatKitItem, ChatKitThread


@dataclass(frozen=True)
class ChatKitRequestContext:
    user_id: str


ThreadAdapter = TypeAdapter(ThreadMetadata)
ThreadItemAdapter = TypeAdapter(ThreadItem)
AttachmentAdapter = TypeAdapter(Attachment)


class SQLAlchemyChatKitStore(Store[ChatKitRequestContext]):
    async def load_thread(
        self,
        thread_id: str,
        context: ChatKitRequestContext,
    ) -> ThreadMetadata:
        async with AsyncSessionLocal() as db:
            row = await db.get(ChatKitThread, thread_id)
            if not row or row.user_id != context.user_id:
                raise NotFoundError(f"Thread not found: {thread_id}")
            return ThreadAdapter.validate_json(row.payload)

    async def save_thread(
        self,
        thread: ThreadMetadata,
        context: ChatKitRequestContext,
    ) -> None:
        async with AsyncSessionLocal() as db:
            row = await db.get(ChatKitThread, thread.id)
            payload = thread.model_dump_json(exclude_none=True)
            if row:
                if row.user_id != context.user_id:
                    raise NotFoundError(f"Thread not found: {thread.id}")
                row.payload = payload
            else:
                db.add(
                    ChatKitThread(
                        id=thread.id,
                        user_id=context.user_id,
                        payload=payload,
                    )
                )
            await db.commit()

    async def load_thread_items(
        self,
        thread_id: str,
        after: str | None,
        limit: int,
        order: str,
        context: ChatKitRequestContext,
    ) -> Page[ThreadItem]:
        async with AsyncSessionLocal() as db:
            rows = await self._load_item_rows(db, thread_id, context.user_id, order)

        start = 0
        if after:
            for index, row in enumerate(rows):
                if row.item_id == after:
                    start = index + 1
                    break

        page_rows = rows[start : start + limit]
        has_more = start + limit < len(rows)
        page_after = page_rows[-1].item_id if has_more and page_rows else None
        return Page(
            data=[ThreadItemAdapter.validate_json(row.payload) for row in page_rows],
            has_more=has_more,
            after=page_after,
        )

    async def save_attachment(
        self,
        attachment: Attachment,
        context: ChatKitRequestContext,
    ) -> None:
        async with AsyncSessionLocal() as db:
            row = await db.get(ChatKitAttachment, attachment.id)
            payload = AttachmentAdapter.dump_json(attachment).decode("utf-8")
            if row:
                if row.user_id != context.user_id:
                    raise NotFoundError(f"Attachment not found: {attachment.id}")
                row.payload = payload
            else:
                db.add(
                    ChatKitAttachment(
                        id=attachment.id,
                        user_id=context.user_id,
                        payload=payload,
                    )
                )
            await db.commit()

    async def load_attachment(
        self,
        attachment_id: str,
        context: ChatKitRequestContext,
    ) -> Attachment:
        async with AsyncSessionLocal() as db:
            row = await db.get(ChatKitAttachment, attachment_id)
            if not row or row.user_id != context.user_id:
                raise NotFoundError(f"Attachment not found: {attachment_id}")
            return AttachmentAdapter.validate_json(row.payload)

    async def delete_attachment(
        self,
        attachment_id: str,
        context: ChatKitRequestContext,
    ) -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(ChatKitAttachment).where(
                    ChatKitAttachment.id == attachment_id,
                    ChatKitAttachment.user_id == context.user_id,
                )
            )
            await db.commit()

    async def load_threads(
        self,
        limit: int,
        after: str | None,
        order: str,
        context: ChatKitRequestContext,
    ) -> Page[ThreadMetadata]:
        async with AsyncSessionLocal() as db:
            stmt = select(ChatKitThread).where(ChatKitThread.user_id == context.user_id)
            stmt = stmt.order_by(
                ChatKitThread.created_at.asc()
                if order == "asc"
                else ChatKitThread.created_at.desc()
            )
            rows = list((await db.execute(stmt)).scalars())

        start = 0
        if after:
            for index, row in enumerate(rows):
                if row.id == after:
                    start = index + 1
                    break

        page_rows = rows[start : start + limit]
        has_more = start + limit < len(rows)
        page_after = page_rows[-1].id if has_more and page_rows else None
        return Page(
            data=[ThreadAdapter.validate_json(row.payload) for row in page_rows],
            has_more=has_more,
            after=page_after,
        )

    async def add_thread_item(
        self,
        thread_id: str,
        item: ThreadItem,
        context: ChatKitRequestContext,
    ) -> None:
        await self.save_item(thread_id, item, context)

    async def save_item(
        self,
        thread_id: str,
        item: ThreadItem,
        context: ChatKitRequestContext,
    ) -> None:
        async with AsyncSessionLocal() as db:
            stmt = select(ChatKitItem).where(
                ChatKitItem.thread_id == thread_id,
                ChatKitItem.item_id == item.id,
                ChatKitItem.user_id == context.user_id,
            )
            row = (await db.execute(stmt)).scalar_one_or_none()
            payload = ThreadItemAdapter.dump_json(item).decode("utf-8")
            if row:
                row.payload = payload
            else:
                db.add(
                    ChatKitItem(
                        thread_id=thread_id,
                        item_id=item.id,
                        user_id=context.user_id,
                        payload=payload,
                    )
                )
            await db.commit()

    async def load_item(
        self,
        thread_id: str,
        item_id: str,
        context: ChatKitRequestContext,
    ) -> ThreadItem:
        async with AsyncSessionLocal() as db:
            stmt = select(ChatKitItem).where(
                ChatKitItem.thread_id == thread_id,
                ChatKitItem.item_id == item_id,
                ChatKitItem.user_id == context.user_id,
            )
            row = (await db.execute(stmt)).scalar_one_or_none()
            if not row:
                raise NotFoundError(f"Thread item not found: {item_id}")
            return ThreadItemAdapter.validate_json(row.payload)

    async def delete_thread(
        self,
        thread_id: str,
        context: ChatKitRequestContext,
    ) -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(ChatKitItem).where(
                    ChatKitItem.thread_id == thread_id,
                    ChatKitItem.user_id == context.user_id,
                )
            )
            await db.execute(
                delete(ChatKitThread).where(
                    ChatKitThread.id == thread_id,
                    ChatKitThread.user_id == context.user_id,
                )
            )
            await db.commit()

    async def delete_thread_item(
        self,
        thread_id: str,
        item_id: str,
        context: ChatKitRequestContext,
    ) -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(ChatKitItem).where(
                    ChatKitItem.thread_id == thread_id,
                    ChatKitItem.item_id == item_id,
                    ChatKitItem.user_id == context.user_id,
                )
            )
            await db.commit()

    async def _load_item_rows(
        self,
        db: AsyncSession,
        thread_id: str,
        user_id: str,
        order: str,
    ) -> list[ChatKitItem]:
        stmt = select(ChatKitItem).where(
            ChatKitItem.thread_id == thread_id,
            ChatKitItem.user_id == user_id,
        )
        stmt = stmt.order_by(
            ChatKitItem.row_id.asc() if order == "asc" else ChatKitItem.row_id.desc()
        )
        return list((await db.execute(stmt)).scalars())
