"""投递、工作项与领域事件仓储。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update

from tyrant_core.models import (
    Delivery,
    DeliveryState,
    DomainEvent,
    WorkItem,
    WorkItemStatus,
)

from .codec import dt_to_str, model_to_row, normalize_value, row_to_model
from .support import SqliteRepositorySupport
from .tables import deliveries_table, domain_events_table, work_items_table


class DeliveryRepository(SqliteRepositorySupport):
    """负责 Delivery / WorkItem / DomainEvent。"""

    async def save_delivery(self, delivery: Delivery) -> None:
        await self.upsert(deliveries_table, model_to_row(delivery))

    async def get_delivery(self, delivery_id: str) -> Delivery | None:
        result = await self.session().execute(
            select(deliveries_table).where(deliveries_table.c.id == delivery_id)
        )
        return row_to_model(Delivery, result.mappings().first())

    async def lease_deliveries(
        self,
        *,
        recipient_actor_id: str,
        lease_owner: str,
        limit: int = 1,
        lease_seconds: int = 30,
    ) -> list[Delivery]:
        now = datetime.now(timezone.utc)
        now_str = dt_to_str(now)
        lease_expires_at = dt_to_str(now + timedelta(seconds=lease_seconds))
        stmt = (
            select(deliveries_table)
            .where(deliveries_table.c.recipient_actor_id == recipient_actor_id)
            .where(
                or_(
                    deliveries_table.c.state.in_(
                        [DeliveryState.PENDING.value, DeliveryState.FAILED.value]
                    ),
                    and_(
                        deliveries_table.c.state == DeliveryState.LEASED.value,
                        or_(
                            deliveries_table.c.lease_expires_at.is_(None),
                            deliveries_table.c.lease_expires_at <= now_str,
                        ),
                    ),
                )
            )
            .where(
                or_(
                    deliveries_table.c.next_retry_at.is_(None),
                    deliveries_table.c.next_retry_at <= now_str,
                )
            )
            .order_by(deliveries_table.c.created_at.asc())
            .limit(limit)
        )
        result = await self.session().execute(stmt)
        leased: list[Delivery] = []
        for row in result.mappings().all():
            current = row_to_model(Delivery, row)
            updated = current.model_copy(
                update={
                    "state": DeliveryState.LEASED,
                    "attempt_count": current.attempt_count + 1,
                    "lease_owner": lease_owner,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                }
            )
            write_result = await self.session().execute(
                update(deliveries_table)
                .where(deliveries_table.c.id == current.id)
                .where(deliveries_table.c.state == current.state.value)
                .where(deliveries_table.c.attempt_count == current.attempt_count)
                .values(
                    state=DeliveryState.LEASED.value,
                    attempt_count=current.attempt_count + 1,
                    lease_owner=lease_owner,
                    lease_expires_at=lease_expires_at,
                )
            )
            if write_result.rowcount:
                leased.append(updated)
        return leased

    async def ack_delivery(self, delivery_id: str) -> None:
        await self.session().execute(
            update(deliveries_table)
            .where(deliveries_table.c.id == delivery_id)
            .values(
                state=DeliveryState.ACKED.value,
                acked_at=dt_to_str(datetime.now(timezone.utc)),
                lease_owner=None,
                lease_expires_at=None,
            )
        )

    async def fail_delivery(self, delivery_id: str, error: dict | None = None) -> None:
        await self.session().execute(
            update(deliveries_table)
            .where(deliveries_table.c.id == delivery_id)
            .values(
                state=DeliveryState.FAILED.value,
                lease_owner=None,
                lease_expires_at=None,
                last_error=error,
            )
        )

    async def query_deliveries(
        self,
        *,
        recipient_actor_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[Delivery]:
        stmt = select(deliveries_table)
        if recipient_actor_id is not None:
            stmt = stmt.where(deliveries_table.c.recipient_actor_id == recipient_actor_id)
        if state is not None:
            stmt = stmt.where(deliveries_table.c.state == state)
        stmt = stmt.order_by(deliveries_table.c.created_at.desc()).limit(limit)
        result = await self.session().execute(stmt)
        return [row_to_model(Delivery, row) for row in result.mappings().all()]

    async def save_work_item(self, work_item: WorkItem) -> None:
        await self.upsert(work_items_table, model_to_row(work_item))

    async def get_work_item(self, work_item_id: str) -> WorkItem | None:
        result = await self.session().execute(
            select(work_items_table).where(work_items_table.c.id == work_item_id)
        )
        return row_to_model(WorkItem, result.mappings().first())

    async def update_work_item(self, work_item_id: str, **fields) -> None:
        values = {key: normalize_value(value) for key, value in fields.items()}
        if not values:
            return
        await self.session().execute(
            update(work_items_table)
            .where(work_items_table.c.id == work_item_id)
            .values(**values)
        )

    async def claim_work_item(
        self,
        work_item_id: str,
        *,
        owner_actor_id: str | None = None,
    ) -> bool:
        stmt = (
            update(work_items_table)
            .where(work_items_table.c.id == work_item_id)
            .where(work_items_table.c.status == WorkItemStatus.PENDING.value)
        )
        if owner_actor_id is not None:
            stmt = stmt.where(work_items_table.c.owner_actor_id == owner_actor_id)
        result = await self.session().execute(
            stmt.values(
                status=WorkItemStatus.IN_PROGRESS.value,
                updated_at=dt_to_str(datetime.now(timezone.utc)),
            )
        )
        return bool(result.rowcount)

    async def query_work_items(
        self,
        *,
        owner_actor_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[WorkItem]:
        stmt = select(work_items_table)
        if owner_actor_id is not None:
            stmt = stmt.where(work_items_table.c.owner_actor_id == owner_actor_id)
        if status is not None:
            stmt = stmt.where(work_items_table.c.status == status)
        stmt = stmt.order_by(work_items_table.c.updated_at.desc()).limit(limit)
        result = await self.session().execute(stmt)
        return [row_to_model(WorkItem, row) for row in result.mappings().all()]

    async def append_domain_event(self, event_model: DomainEvent) -> None:
        await self.upsert(domain_events_table, model_to_row(event_model))

    async def get_domain_events(
        self,
        *,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[DomainEvent]:
        stmt = select(domain_events_table)
        if since is not None:
            stmt = stmt.where(domain_events_table.c.created_at >= dt_to_str(since))
        stmt = stmt.order_by(domain_events_table.c.created_at.desc()).limit(limit)
        result = await self.session().execute(stmt)
        return [row_to_model(DomainEvent, row) for row in result.mappings().all()]
