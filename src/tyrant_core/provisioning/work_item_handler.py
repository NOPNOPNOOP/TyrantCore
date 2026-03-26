"""Provisioning 工单处理器。"""

from __future__ import annotations

from datetime import datetime, timezone

from tyrant_core.management.reconciler import ActorReconciler
from tyrant_core.models import (
    ProvisionRequestNotFoundError,
    ValidationError,
    WorkItemNotFoundError,
    WorkItemStatus,
)
from tyrant_core.store import SqliteStore, Store

from .service import ProvisioningService


class ProvisioningWorkItemHandler:
    """把 PROVISION_ACTOR 工单推进为真正的创建执行。"""

    def __init__(
        self,
        provisioning: ProvisioningService,
        *,
        store: Store | None = None,
        reconciler: ActorReconciler | None = None,
    ) -> None:
        self.provisioning = provisioning
        self.store = store or provisioning.store or SqliteStore()
        self.reconciler = reconciler

    async def process(
        self,
        work_item_id: str,
        *,
        executor_actor_id: str,
        auto_reconcile: bool = True,
    ) -> dict:
        now = datetime.now(timezone.utc)
        async with self.store.transaction():
            work_item = await self.store.get_work_item(work_item_id)
            if work_item is None:
                raise WorkItemNotFoundError(work_item_id)
            request_id = (
                work_item.result.get("provision_request_id")
                or work_item.spec.get("provision_request_id")
            )
            if not request_id:
                raise ValidationError("work item is missing provision_request_id")
            await self.store.update_work_item(
                work_item_id,
                status=WorkItemStatus.IN_PROGRESS,
                updated_at=now,
            )

        request = await self.provisioning.get_request(request_id)
        if request is None:
            await self._fail_work_item(
                work_item_id,
                error=f"provision request not found: {request_id}",
            )
            raise ProvisionRequestNotFoundError(request_id)

        try:
            if request.status.value == "SUCCEEDED":
                records = await self.provisioning.query_records(request_id=request_id, limit=1)
                if not records:
                    raise ValidationError("provision request succeeded without record")
                record = records[0]
            else:
                record = await self.provisioning.execute_request(
                    request_id,
                    executor_actor_id=executor_actor_id,
                )

            reconciled = False
            if (
                auto_reconcile
                and self.reconciler is not None
                and record.produced_actor_id is not None
            ):
                reconciled = await self.reconciler.reconcile_actor(record.produced_actor_id)

            closed_at = datetime.now(timezone.utc)
            async with self.store.transaction():
                latest = await self.store.get_work_item(work_item_id)
                current_result = {} if latest is None else dict(latest.result)
                current_result.update({
                    "provision_request_id": request_id,
                    "provision_record_id": record.id,
                    "produced_actor_id": record.produced_actor_id,
                    "reconciled": reconciled,
                })
                await self.store.update_work_item(
                    work_item_id,
                    status=WorkItemStatus.COMPLETED,
                    result=current_result,
                    updated_at=closed_at,
                    closed_at=closed_at,
                )
                updated = await self.store.get_work_item(work_item_id)

            return {
                "work_item": None if updated is None else updated.model_dump(mode="json"),
                "provision_record": record.model_dump(mode="json"),
                "reconciled": reconciled,
            }
        except Exception as exc:
            await self._fail_work_item(work_item_id, error=str(exc))
            raise

    async def _fail_work_item(self, work_item_id: str, *, error: str) -> None:
        now = datetime.now(timezone.utc)
        async with self.store.transaction():
            latest = await self.store.get_work_item(work_item_id)
            if latest is None:
                return
            current_result = dict(latest.result)
            current_result["error"] = error
            await self.store.update_work_item(
                work_item_id,
                status=WorkItemStatus.FAILED,
                result=current_result,
                updated_at=now,
                closed_at=now,
            )
