"""创建域仓储。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from tyrant_core.models import ProvisionRecord, ProvisionRequest, ProvisionStatus

from .codec import dt_to_str, model_to_row, normalize_value, row_to_model
from .support import SqliteRepositorySupport
from .tables import provision_records_table, provision_requests_table


class ProvisioningRepository(SqliteRepositorySupport):
    """负责 ProvisionRequest / ProvisionRecord。"""

    async def save_provision_request(self, request: ProvisionRequest) -> None:
        await self.upsert(provision_requests_table, model_to_row(request))

    async def get_provision_request(
        self,
        request_id: str,
    ) -> ProvisionRequest | None:
        result = await self.session().execute(
            select(provision_requests_table).where(provision_requests_table.c.id == request_id)
        )
        return row_to_model(ProvisionRequest, result.mappings().first())

    async def claim_provision_request(
        self,
        request_id: str,
        executor_actor_id: str,
    ) -> bool:
        now = dt_to_str(datetime.now(timezone.utc))
        result = await self.session().execute(
            update(provision_requests_table)
            .where(provision_requests_table.c.id == request_id)
            .where(provision_requests_table.c.status == ProvisionStatus.PENDING.value)
            .values(
                status=ProvisionStatus.CLAIMED.value,
                claimed_by_actor_id=executor_actor_id,
                claimed_at=now,
            )
        )
        return bool(result.rowcount)

    async def update_provision_request(self, request_id: str, **fields) -> None:
        values = {key: normalize_value(value) for key, value in fields.items()}
        if not values:
            return
        await self.session().execute(
            update(provision_requests_table)
            .where(provision_requests_table.c.id == request_id)
            .values(**values)
        )

    async def query_provision_requests(
        self,
        *,
        status: str | None = None,
        requested_by_actor_id: str | None = None,
        target_parent_actor_id: str | None = None,
        limit: int = 50,
    ) -> list[ProvisionRequest]:
        stmt = select(provision_requests_table)
        if status is not None:
            stmt = stmt.where(provision_requests_table.c.status == status)
        if requested_by_actor_id is not None:
            stmt = stmt.where(
                provision_requests_table.c.requested_by_actor_id == requested_by_actor_id
            )
        if target_parent_actor_id is not None:
            stmt = stmt.where(
                provision_requests_table.c.target_parent_actor_id == target_parent_actor_id
            )
        stmt = stmt.order_by(provision_requests_table.c.created_at.desc()).limit(limit)
        result = await self.session().execute(stmt)
        return [row_to_model(ProvisionRequest, row) for row in result.mappings().all()]

    async def save_provision_record(self, record: ProvisionRecord) -> None:
        await self.upsert(provision_records_table, model_to_row(record))

    async def get_provision_record(self, record_id: str) -> ProvisionRecord | None:
        result = await self.session().execute(
            select(provision_records_table).where(provision_records_table.c.id == record_id)
        )
        return row_to_model(ProvisionRecord, result.mappings().first())

    async def update_provision_record(self, record_id: str, **fields) -> None:
        values = {key: normalize_value(value) for key, value in fields.items()}
        if not values:
            return
        await self.session().execute(
            update(provision_records_table)
            .where(provision_records_table.c.id == record_id)
            .values(**values)
        )

    async def query_provision_records(
        self,
        *,
        request_id: str | None = None,
        produced_actor_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[ProvisionRecord]:
        stmt = select(provision_records_table)
        if request_id is not None:
            stmt = stmt.where(provision_records_table.c.request_id == request_id)
        if produced_actor_id is not None:
            stmt = stmt.where(provision_records_table.c.produced_actor_id == produced_actor_id)
        if state is not None:
            stmt = stmt.where(provision_records_table.c.state == state)
        stmt = stmt.order_by(provision_records_table.c.created_at.desc()).limit(limit)
        result = await self.session().execute(stmt)
        return [row_to_model(ProvisionRecord, row) for row in result.mappings().all()]
