"""v2 SystemAPI。

SystemAPI 现在是命令侧与查询侧的组合边界，而不是 v1 的巨型业务类。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from tyrant_core.bus import EventBus
from tyrant_core.api.command_api import CommandAPI
from tyrant_core.api.policies import PolicyDecision, PolicyEngine
from tyrant_core.api.query_api import QueryAPI
from tyrant_core.management.reconciler import ActorReconciler
from tyrant_core.management.service import ActorManagementService
from tyrant_core.models import UnsupportedLegacyComponentError
from tyrant_core.provisioning.base import ProvisioningQueueProcessorLike
from tyrant_core.provisioning.queue_processor import ProvisioningQueueProcessor
from tyrant_core.provisioning.service import ProvisioningService
from tyrant_core.provisioning.work_item_handler import ProvisioningWorkItemHandler
from tyrant_core.store import SqliteStore, Store

if TYPE_CHECKING:
    from tyrant_core.runtime.runtime_supervisor import RuntimeSupervisor


class SystemAPI:
    """v2 对外统一入口。"""

    def __init__(
        self,
        store: Store | None = None,
        event_bus: EventBus | None = None,
        policies: PolicyEngine | None = None,
        commands: CommandAPI | None = None,
        queries: QueryAPI | None = None,
        management: ActorManagementService | None = None,
        provisioning: ProvisioningService | None = None,
        provisioning_work_items: ProvisioningWorkItemHandler | None = None,
        provisioning_queue: ProvisioningQueueProcessorLike | None = None,
        agent_factory: ProvisioningQueueProcessorLike | None = None,
        runtime_supervisor: "RuntimeSupervisor" | None = None,
        reconciler: ActorReconciler | None = None,
    ) -> None:
        self.store = store or SqliteStore()
        self.event_bus = event_bus or EventBus()
        self.policies = policies or PolicyEngine(self.store)
        self.commands = commands or CommandAPI(
            self.store,
            self.event_bus,
            self.policies,
        )
        self.queries = queries or QueryAPI(self.store)
        self.management = management or ActorManagementService(self.store)
        self.provisioning = provisioning or ProvisioningService(self.store)
        self.runtime_supervisor = runtime_supervisor
        self.reconciler = reconciler
        self.provisioning_work_items = provisioning_work_items or ProvisioningWorkItemHandler(
            self.provisioning,
            store=self.store,
            reconciler=self.reconciler,
        )
        if provisioning_queue is not None:
            self.provisioning_queue = provisioning_queue
        elif agent_factory is not None:
            self.provisioning_queue = agent_factory
        elif all(
            hasattr(self.store, attr)
            for attr in ("transaction", "query_work_items", "claim_work_item")
        ):
            self.provisioning_queue = ProvisioningQueueProcessor(
                store=self.store,
                handler=self.provisioning_work_items,
            )
        else:
            self.provisioning_queue = None
        self.agent_factory = self.provisioning_queue

    @staticmethod
    def _dump(value):
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return value

    @classmethod
    def _dump_list(cls, values):
        return [cls._dump(value) for value in values]

    def _require_runtime_supervisor(self) -> "RuntimeSupervisor":
        if self.runtime_supervisor is None:
            raise UnsupportedLegacyComponentError("runtime supervisor is not configured")
        return self.runtime_supervisor

    def _require_reconciler(self) -> ActorReconciler:
        if self.reconciler is None:
            raise UnsupportedLegacyComponentError("actor reconciler is not configured")
        return self.reconciler

    def _require_provisioning_queue(self) -> ProvisioningQueueProcessorLike:
        if self.provisioning_queue is None:
            raise UnsupportedLegacyComponentError("provisioning queue is not configured")
        return self.provisioning_queue

    def _require_agent_factory(self) -> ProvisioningQueueProcessorLike:
        return self._require_provisioning_queue()

    async def create_actor(self, spec: dict) -> dict:
        result = await self.commands.create_actor(spec)
        return result.payload

    async def create_org_edge(self, spec: dict) -> dict:
        result = await self.commands.create_org_edge(spec)
        return result.payload

    async def issue_grant(self, spec: dict) -> dict:
        result = await self.commands.issue_grant(spec)
        return result.payload

    async def open_thread(self, spec: dict) -> dict:
        result = await self.commands.open_thread(spec)
        return result.payload

    async def send_envelope(self, spec: dict) -> dict:
        result = await self.commands.send_envelope(spec)
        return result.payload

    async def create_work_item(self, spec: dict) -> dict:
        result = await self.commands.create_work_item(spec)
        return result.payload

    async def register_attachment(self, spec: dict) -> dict:
        result = await self.commands.register_attachment(spec)
        return result.payload

    async def report_presence(self, spec: dict) -> dict:
        result = await self.commands.report_presence(spec)
        return result.payload

    async def create_actor_profile(self, spec: dict) -> dict:
        return self._dump(await self.management.create_profile(spec))

    async def get_actor_profile(self, profile_id: str) -> dict | None:
        return self._dump(await self.management.get_profile(profile_id))

    async def query_actor_profiles(
        self,
        *,
        archived: bool | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return self._dump_list(
            await self.management.query_profiles(archived=archived, limit=limit)
        )

    async def create_actor_control(self, spec: dict) -> dict:
        return self._dump(await self.management.create_control(spec))

    async def get_actor_control(self, actor_id: str) -> dict | None:
        return self._dump(await self.management.get_control(actor_id))

    async def query_actor_controls(
        self,
        *,
        manager_actor_id: str | None = None,
        desired_state: str | None = None,
        runtime_kind: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return self._dump_list(
            await self.management.query_controls(
                manager_actor_id=manager_actor_id,
                desired_state=desired_state,
                runtime_kind=runtime_kind,
                limit=limit,
            )
        )

    async def assign_actor_manager(self, actor_id: str, manager_actor_id: str) -> dict:
        return self._dump(await self.management.assign_manager(actor_id, manager_actor_id))

    async def set_actor_profile(self, actor_id: str, profile_id: str | None) -> dict:
        return self._dump(await self.management.set_profile(actor_id, profile_id))

    async def set_actor_desired_state(self, actor_id: str, desired_state: str) -> dict:
        return self._dump(await self.management.set_desired_state(actor_id, desired_state))

    async def submit_provision_request(self, spec: dict) -> dict:
        return self._dump(await self.provisioning.submit_request(spec))

    async def get_provision_request(self, request_id: str) -> dict | None:
        return self._dump(await self.provisioning.get_request(request_id))

    async def claim_provision_request(
        self,
        request_id: str,
        executor_actor_id: str,
    ) -> bool:
        return await self.provisioning.claim_request(request_id, executor_actor_id)

    async def query_provision_requests(self, **filters) -> list[dict]:
        return self._dump_list(await self.provisioning.query_requests(**filters))

    async def execute_provision_request(
        self,
        request_id: str,
        *,
        executor_actor_id: str | None = None,
    ) -> dict:
        return self._dump(
            await self.provisioning.execute_request(
                request_id,
                executor_actor_id=executor_actor_id,
            )
        )

    async def get_provision_record(self, record_id: str) -> dict | None:
        return self._dump(await self.provisioning.get_record(record_id))

    async def query_provision_records(self, **filters) -> list[dict]:
        return self._dump_list(await self.provisioning.query_records(**filters))

    async def request_actor_provision(self, spec: dict) -> dict:
        blueprint = spec.get("blueprint", {})
        blueprint_payload = self._dump(blueprint)
        blueprint_name = (
            blueprint_payload.get("name")
            if isinstance(blueprint_payload, dict)
            else None
        )
        work_item_spec = {
            "kind": spec.get("work_item_kind", "PROVISION_ACTOR"),
            "creator_actor_id": spec.get(
                "creator_actor_id",
                spec["requested_by_actor_id"],
            ),
            "owner_actor_id": spec.get(
                "work_item_owner_actor_id",
                spec["requested_by_actor_id"],
            ),
            "thread_id": spec.get("thread_id"),
            "parent_work_item_id": spec.get("parent_work_item_id"),
            "subject": spec.get(
                "subject",
                blueprint_name or "PROVISION_ACTOR",
            ),
            "spec": {
                "blueprint": blueprint_payload,
                "reason": spec.get("reason"),
                "provisioner_hint": spec.get("provisioner_hint"),
            },
            "caller_actor_id": spec.get(
                "caller_actor_id",
                spec["requested_by_actor_id"],
            ),
            "bypass_policy": spec.get("bypass_policy", False),
        }
        if spec.get("work_item_id") is not None:
            work_item_spec["id"] = spec["work_item_id"]
        work_item = await self.create_work_item(work_item_spec)
        work_item_id = work_item["work_item"]["id"]
        request = await self.submit_provision_request({
            **spec,
            "work_item_id": work_item_id,
        })
        updated_work_item = work_item["work_item"]
        if hasattr(self.store, "transaction") and hasattr(self.store, "update_work_item"):
            async with self.store.transaction():
                await self.store.update_work_item(
                    work_item_id,
                    result={"provision_request_id": request["id"]},
                    updated_at=datetime.now(timezone.utc),
                )
                latest_work_item = await self.store.get_work_item(work_item_id)
                if latest_work_item is not None:
                    updated_work_item = self._dump(latest_work_item)
        return {
            "work_item": updated_work_item,
            "provision_request": request,
        }

    async def process_provision_work_item(
        self,
        work_item_id: str,
        *,
        executor_actor_id: str,
        auto_reconcile: bool = True,
    ) -> dict:
        return await self.provisioning_work_items.process(
            work_item_id,
            executor_actor_id=executor_actor_id,
            auto_reconcile=auto_reconcile,
        )

    async def process_next_provision_work_item(
        self,
        *,
        owner_actor_id: str,
        executor_actor_id: str,
        auto_reconcile: bool = True,
        scan_limit: int = 20,
    ) -> dict | None:
        return await self._require_provisioning_queue().process_next(
            owner_actor_id=owner_actor_id,
            executor_actor_id=executor_actor_id,
            auto_reconcile=auto_reconcile,
            scan_limit=scan_limit,
        )

    async def spawn_actor_runtime(self, actor_id: str) -> dict:
        return await self._require_runtime_supervisor().spawn_actor_runtime(actor_id)

    async def stop_actor_runtime(self, actor_id: str) -> None:
        await self._require_runtime_supervisor().stop_actor_runtime(actor_id)

    async def get_runtime_status(self, actor_id: str) -> dict | None:
        return await self._require_runtime_supervisor().get_runtime_status(actor_id)

    async def reconcile_actor(self, actor_id: str) -> bool:
        return await self._require_reconciler().reconcile_actor(actor_id)

    async def reconcile_all(self, *, limit: int = 1000) -> int:
        return await self._require_reconciler().reconcile_all(limit=limit)

    async def get_actor(self, actor_id: str) -> dict:
        return await self.queries.get_actor(actor_id)

    async def list_actors(self, **filters) -> list[dict]:
        return await self.queries.list_actors(**filters)

    async def list_org_edges(self, **filters) -> list[dict]:
        return await self.queries.list_org_edges(**filters)

    async def list_grants(self, subject_actor_id: str) -> list[dict]:
        return await self.queries.list_grants(subject_actor_id)

    async def get_thread(self, thread_id: str) -> dict:
        return await self.queries.get_thread(thread_id)

    async def get_envelope(self, envelope_id: str) -> dict:
        return await self.queries.get_envelope(envelope_id)

    async def query_envelopes(
        self,
        *,
        thread_id: str | None = None,
        sender_actor_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.queries.query_envelopes(
            thread_id=thread_id,
            sender_actor_id=sender_actor_id,
            limit=limit,
        )

    async def get_delivery(self, delivery_id: str) -> dict:
        return await self.queries.get_delivery(delivery_id)

    async def list_pending_deliveries(self, actor_id: str) -> list[dict]:
        return await self.queries.list_pending_deliveries(actor_id)

    async def query_deliveries(
        self,
        *,
        actor_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.queries.query_deliveries(
            actor_id=actor_id,
            state=state,
            limit=limit,
        )

    async def get_work_item(self, work_item_id: str) -> dict:
        return await self.queries.get_work_item(work_item_id)

    async def query_work_items(
        self,
        *,
        owner_actor_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.queries.query_work_items(
            owner_actor_id=owner_actor_id,
            status=status,
            limit=limit,
        )

    async def query_sessions(
        self,
        *,
        actor_id: str | None = None,
        surface: str | None = None,
        external_key: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.queries.query_sessions(
            actor_id=actor_id,
            surface=surface,
            external_key=external_key,
            state=state,
            limit=limit,
        )

    async def query_attachments(
        self,
        *,
        actor_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.queries.query_attachments(
            actor_id=actor_id,
            kind=kind,
            limit=limit,
        )

    async def get_presence(self, attachment_id: str) -> dict | None:
        return await self.queries.get_presence(attachment_id)

    async def get_domain_events(self, *, since=None, limit: int = 100) -> list[dict]:
        return await self.queries.get_domain_events(since=since, limit=limit)

    async def can(
        self,
        action: str,
        subject_actor_id: str,
        target_id: str | None = None,
    ) -> PolicyDecision:
        return await self.policies.can(action, subject_actor_id, target_id=target_id)
