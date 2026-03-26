"""创建工单队列处理器。"""

from __future__ import annotations

from tyrant_core.models import WorkItemStatus
from tyrant_core.store import SqliteStore, Store

from .service import ProvisioningService
from .work_item_handler import ProvisioningWorkItemHandler


class ProvisioningQueueProcessor:
    """从工单队列领取下一条 PROVISION_ACTOR 并驱动 handler。"""

    def __init__(
        self,
        *,
        store: Store | None = None,
        provisioning: ProvisioningService | None = None,
        handler: ProvisioningWorkItemHandler | None = None,
        reconciler=None,
    ) -> None:
        if handler is not None:
            self.handler = handler
            self.store = store or handler.store
            return

        self.store = store or SqliteStore()
        self.handler = ProvisioningWorkItemHandler(
            provisioning or ProvisioningService(self.store),
            store=self.store,
            reconciler=reconciler,
        )

    async def process_next(
        self,
        *,
        owner_actor_id: str,
        executor_actor_id: str,
        auto_reconcile: bool = True,
        scan_limit: int = 20,
    ) -> dict | None:
        """领取并处理下一条 PROVISION_ACTOR 工单。"""
        async with self.store.transaction():
            work_items = await self.store.query_work_items(
                owner_actor_id=owner_actor_id,
                status=WorkItemStatus.PENDING.value,
                limit=scan_limit,
            )

        for work_item in work_items:
            if work_item.kind != "PROVISION_ACTOR":
                continue
            async with self.store.transaction():
                claimed = await self.store.claim_work_item(
                    work_item.id,
                    owner_actor_id=owner_actor_id,
                )
            if not claimed:
                continue
            return await self.handler.process(
                work_item.id,
                executor_actor_id=executor_actor_id,
                auto_reconcile=auto_reconcile,
            )
        return None
