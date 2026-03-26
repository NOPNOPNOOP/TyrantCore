"""Gateway session 子控制器。"""

from __future__ import annotations

from typing import Any

from tyrant_core.gateway.session_registry import GatewaySession, SessionRegistry


class GatewaySessionControl:
    """负责 session 生命周期与查询。"""

    def __init__(self, sessions: SessionRegistry) -> None:
        self.sessions = sessions

    async def open_session(
        self,
        *,
        surface: str,
        actor_id: str,
        external_key: str,
        thread_id: str | None = None,
        mode: str = "PERSISTENT",
        binding_id: str | None = None,
        metadata: dict | None = None,
    ) -> GatewaySession:
        return await self.sessions.open_session(
            surface=surface,
            actor_id=actor_id,
            external_key=external_key,
            thread_id=thread_id,
            mode=mode,
            binding_id=binding_id,
            metadata=metadata,
        )

    async def bind_session_thread(
        self,
        session_id: str,
        thread_id: str,
    ) -> GatewaySession | None:
        return await self.sessions.bind_thread(session_id, thread_id)

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        session = await self.sessions.get(session_id)
        if session is None:
            return None
        return session.model_dump(mode="json")

    async def query_sessions(
        self,
        *,
        actor_id: str | None = None,
        surface: str | None = None,
        external_key: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        sessions = await self.sessions.query(
            actor_id=actor_id,
            surface=surface,
            external_key=external_key,
            state=state,
            limit=limit,
        )
        return [session.model_dump(mode="json") for session in sessions]
