"""Gateway 会话注册表。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from tyrant_core.models import Session, SessionMode, SessionState
from tyrant_core.store import Store

GatewaySession = Session


class SessionRegistry:
    """基于 Store 的 session registry。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    async def open_session(
        self,
        *,
        surface: str,
        actor_id: str,
        external_key: str,
        thread_id: str | None = None,
        mode: str = "PERSISTENT",
        binding_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GatewaySession:
        now = datetime.now(timezone.utc)
        requested_mode = SessionMode(mode)
        async with self.store.transaction():
            if requested_mode == SessionMode.PERSISTENT:
                sessions = await self.store.query_sessions(
                    surface=surface,
                    external_key=external_key,
                    limit=20,
                )
                for session in sessions:
                    if session.state == SessionState.CLOSED:
                        continue
                    if session.actor_id != actor_id:
                        continue
                    if session.mode != SessionMode.PERSISTENT:
                        continue
                    if binding_id is not None and session.binding_id not in {None, binding_id}:
                        continue
                    updated = session.model_copy(
                        update={
                            "thread_id": session.thread_id or thread_id,
                            "binding_id": session.binding_id or binding_id,
                            "state": SessionState.OPEN,
                            "last_seen_at": now,
                            "metadata": {
                                **session.metadata,
                                **(metadata or {}),
                            },
                        }
                    )
                    await self.store.save_session(updated)
                    return updated

            created = Session(
                id=str(uuid4()),
                surface=surface,
                binding_id=binding_id,
                actor_id=actor_id,
                external_key=external_key,
                thread_id=thread_id,
                mode=requested_mode,
                state=SessionState.OPEN,
                metadata=dict(metadata or {}),
                created_at=now,
                last_seen_at=now,
                closed_at=None,
            )
            await self.store.save_session(created)
            return created

    async def get(self, session_id: str) -> GatewaySession | None:
        async with self.store.transaction():
            return await self.store.get_session(session_id)

    async def touch(self, session_id: str) -> GatewaySession | None:
        async with self.store.transaction():
            session = await self.store.get_session(session_id)
            if session is None:
                return None
            updated = session.model_copy(
                update={
                    "state": SessionState.OPEN,
                    "last_seen_at": datetime.now(timezone.utc),
                }
            )
            await self.store.save_session(updated)
            return updated

    async def bind_thread(
        self,
        session_id: str,
        thread_id: str,
    ) -> GatewaySession | None:
        async with self.store.transaction():
            session = await self.store.get_session(session_id)
            if session is None:
                return None
            updated = session.model_copy(
                update={
                    "thread_id": thread_id,
                    "last_seen_at": datetime.now(timezone.utc),
                }
            )
            await self.store.save_session(updated)
            return updated

    async def close(self, session_id: str) -> GatewaySession | None:
        async with self.store.transaction():
            session = await self.store.get_session(session_id)
            if session is None:
                return None
            now = datetime.now(timezone.utc)
            updated = session.model_copy(
                update={
                    "state": SessionState.CLOSED,
                    "last_seen_at": now,
                    "closed_at": now,
                }
            )
            await self.store.save_session(updated)
            return updated

    async def query(
        self,
        *,
        actor_id: str | None = None,
        surface: str | None = None,
        external_key: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[GatewaySession]:
        async with self.store.transaction():
            return await self.store.query_sessions(
                actor_id=actor_id,
                surface=surface,
                external_key=external_key,
                state=state,
                limit=limit,
            )
