"""Gateway 领域应用 façade。"""

from __future__ import annotations

from datetime import datetime

from tyrant_core.api import PolicyDecision, SystemAPI


class GatewayApplication:
    """承接核心领域对象的应用层访问。"""

    def __init__(self, api: SystemAPI) -> None:
        self.api = api

    async def create_actor(self, spec: dict) -> dict:
        return await self.api.create_actor(spec)

    async def get_actor(self, actor_id: str) -> dict:
        return await self.api.get_actor(actor_id)

    async def list_actors(self, **filters) -> list[dict]:
        return await self.api.list_actors(**filters)

    async def create_org_edge(self, spec: dict) -> dict:
        return await self.api.create_org_edge(spec)

    async def list_org_edges(self, **filters) -> list[dict]:
        return await self.api.list_org_edges(**filters)

    async def issue_grant(self, spec: dict) -> dict:
        return await self.api.issue_grant(spec)

    async def list_grants(self, subject_actor_id: str) -> list[dict]:
        return await self.api.list_grants(subject_actor_id)

    async def open_thread(self, spec: dict) -> dict:
        return await self.api.open_thread(spec)

    async def get_thread(self, thread_id: str) -> dict:
        return await self.api.get_thread(thread_id)

    async def ingest(self, spec: dict) -> dict:
        return await self.api.send_envelope(spec)

    async def get_envelope(self, envelope_id: str) -> dict:
        return await self.api.get_envelope(envelope_id)

    async def query_envelopes(
        self,
        *,
        thread_id: str | None = None,
        sender_actor_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.api.query_envelopes(
            thread_id=thread_id,
            sender_actor_id=sender_actor_id,
            limit=limit,
        )

    async def create_work_item(self, spec: dict) -> dict:
        return await self.api.create_work_item(spec)

    async def create_actor_profile(self, spec: dict) -> dict:
        return await self.api.create_actor_profile(spec)

    async def get_actor_profile(self, profile_id: str) -> dict | None:
        return await self.api.get_actor_profile(profile_id)

    async def query_actor_profiles(
        self,
        *,
        archived: bool | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.api.query_actor_profiles(archived=archived, limit=limit)

    async def create_actor_control(self, spec: dict) -> dict:
        return await self.api.create_actor_control(spec)

    async def get_actor_control(self, actor_id: str) -> dict | None:
        return await self.api.get_actor_control(actor_id)

    async def query_actor_controls(
        self,
        *,
        manager_actor_id: str | None = None,
        desired_state: str | None = None,
        runtime_kind: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.api.query_actor_controls(
            manager_actor_id=manager_actor_id,
            desired_state=desired_state,
            runtime_kind=runtime_kind,
            limit=limit,
        )

    async def assign_actor_manager(self, actor_id: str, manager_actor_id: str) -> dict:
        return await self.api.assign_actor_manager(actor_id, manager_actor_id)

    async def set_actor_profile(self, actor_id: str, profile_id: str | None) -> dict:
        return await self.api.set_actor_profile(actor_id, profile_id)

    async def set_actor_desired_state(self, actor_id: str, desired_state: str) -> dict:
        return await self.api.set_actor_desired_state(actor_id, desired_state)

    async def submit_provision_request(self, spec: dict) -> dict:
        return await self.api.submit_provision_request(spec)

    async def get_provision_request(self, request_id: str) -> dict | None:
        return await self.api.get_provision_request(request_id)

    async def claim_provision_request(
        self,
        request_id: str,
        executor_actor_id: str,
    ) -> bool:
        return await self.api.claim_provision_request(request_id, executor_actor_id)

    async def query_provision_requests(self, **filters) -> list[dict]:
        return await self.api.query_provision_requests(**filters)

    async def execute_provision_request(
        self,
        request_id: str,
        *,
        executor_actor_id: str | None = None,
    ) -> dict:
        return await self.api.execute_provision_request(
            request_id,
            executor_actor_id=executor_actor_id,
        )

    async def get_provision_record(self, record_id: str) -> dict | None:
        return await self.api.get_provision_record(record_id)

    async def query_provision_records(self, **filters) -> list[dict]:
        return await self.api.query_provision_records(**filters)

    async def request_actor_provision(self, spec: dict) -> dict:
        return await self.api.request_actor_provision(spec)

    async def process_provision_work_item(
        self,
        work_item_id: str,
        *,
        executor_actor_id: str,
        auto_reconcile: bool = True,
    ) -> dict:
        return await self.api.process_provision_work_item(
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
        return await self.api.process_next_provision_work_item(
            owner_actor_id=owner_actor_id,
            executor_actor_id=executor_actor_id,
            auto_reconcile=auto_reconcile,
            scan_limit=scan_limit,
        )

    async def spawn_actor_runtime(self, actor_id: str) -> dict:
        return await self.api.spawn_actor_runtime(actor_id)

    async def stop_actor_runtime(self, actor_id: str) -> None:
        await self.api.stop_actor_runtime(actor_id)

    async def get_runtime_status(self, actor_id: str) -> dict | None:
        return await self.api.get_runtime_status(actor_id)

    async def reconcile_actor(self, actor_id: str) -> bool:
        return await self.api.reconcile_actor(actor_id)

    async def reconcile_all(self, *, limit: int = 1000) -> int:
        return await self.api.reconcile_all(limit=limit)

    async def get_work_item(self, work_item_id: str) -> dict:
        return await self.api.get_work_item(work_item_id)

    async def query_work_items(
        self,
        *,
        owner_actor_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.api.query_work_items(
            owner_actor_id=owner_actor_id,
            status=status,
            limit=limit,
        )

    async def get_domain_events(
        self,
        *,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[dict]:
        return await self.api.get_domain_events(since=since, limit=limit)

    async def can(
        self,
        action: str,
        subject_actor_id: str,
        target_id: str | None = None,
    ) -> PolicyDecision:
        return await self.api.can(action, subject_actor_id, target_id=target_id)
