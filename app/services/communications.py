from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.audit_logs import AuditLogsRepository
from app.repositories.users import UsersRepository
from app.services.exceptions import NotFoundError
from app.services.notifications import NotificationService
from app.services.transactions import transactional


class CommunicationsService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        users_repo: UsersRepository,
        audit_logs_repo: AuditLogsRepository,
        notification_service: NotificationService,
    ) -> None:
        self._session = session
        self._users_repo = users_repo
        self._audit_logs_repo = audit_logs_repo
        self._notification_service = notification_service

    async def send_direct_message(self, *, target: str, text: str, actor_user_id: int | None) -> str:
        user = await self._resolve_user(target)
        if user is None:
            raise NotFoundError("Не получилось найти получателя.")

        await self._notification_service.send_text(
            self._wrap_message(text, urgent=False),
            user=user,
        )
        user_label = self._notification_service.describe_user(user)
        async with transactional(self._session):
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="user",
                entity_id=str(user.id),
                action="direct_message_sent",
                payload_json={
                    "user": user_label,
                    "telegram_user_id": user.telegram_user_id,
                    "vk_user_id": user.vk_user_id,
                    "whatsapp_phone": user.whatsapp_phone,
                    "username": user.username,
                    "text": text,
                },
            )
        return user_label

    async def broadcast_to_customers(self, *, text: str, actor_user_id: int | None) -> dict:
        customers = await self._users_repo.list_customers()
        sent = 0
        failed: list[str] = []
        payload = self._wrap_message(text, urgent=True)

        for user in customers:
            try:
                await self._notification_service.send_text(payload, user=user)
                sent += 1
            except Exception:
                failed.append(self._notification_service.describe_user(user))

        async with transactional(self._session):
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="broadcast",
                entity_id="customers",
                action="broadcast_sent",
                payload_json={
                    "sent": sent,
                    "failed": failed,
                    "total": len(customers),
                    "text": text,
                },
            )
        return {"sent": sent, "failed": failed, "total": len(customers)}

    async def _resolve_user(self, target: str):
        normalized = target.strip()
        normalized_phone = "".join(ch for ch in normalized if ch.isdigit())
        if normalized_phone and len(normalized_phone) >= 10:
            user = await self._users_repo.get_by_whatsapp_phone(normalized_phone)
            if user is not None:
                return user
        if normalized.isdigit():
            return await self._users_repo.get_by_contact_id(int(normalized))
        return await self._users_repo.get_by_username(normalized)

    @staticmethod
    def _wrap_message(text: str, *, urgent: bool) -> str:
        title = "⚠️ <b>Важное сообщение от Юлии</b>" if urgent else "💌 <b>Сообщение от Юлии</b>"
        return f"{title}\n\n{text.strip()}"
