import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis

from app.core.security import new_session_id


@dataclass(frozen=True)
class SessionData:
    sid: str
    user_id: uuid.UUID
    session_version: int
    created_at: datetime
    absolute_expiry: datetime
    last_seen: datetime
    ip: str | None


class RedisSessionStore:
    def __init__(
        self,
        redis: Redis,
        *,
        idle_seconds: int,
        absolute_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._redis = redis
        self._idle_seconds = idle_seconds
        self._absolute_seconds = absolute_seconds
        self._clock = clock or self._default_clock

    async def create(self, *, user_id: uuid.UUID, session_version: int, ip: str | None) -> str:
        sid = new_session_id()
        now = self._now()
        session = SessionData(
            sid=sid,
            user_id=user_id,
            session_version=session_version,
            created_at=now,
            absolute_expiry=now + timedelta(seconds=self._absolute_seconds),
            last_seen=now,
            ip=ip,
        )
        await self._redis.set(self._session_key(sid), self._dump(session), ex=self._idle_seconds)
        await self._redis.sadd(self._user_sessions_key(user_id), sid)
        return sid

    async def get(self, sid: str) -> SessionData | None:
        session = await self._read(sid)
        if session is None:
            return None
        if self._is_absolute_expired(session):
            await self.revoke(sid)
            return None

        refreshed = SessionData(
            sid=session.sid,
            user_id=session.user_id,
            session_version=session.session_version,
            created_at=session.created_at,
            absolute_expiry=session.absolute_expiry,
            last_seen=self._now(),
            ip=session.ip,
        )
        await self._redis.set(self._session_key(sid), self._dump(refreshed), ex=self._idle_seconds)
        return refreshed

    async def revoke(self, sid: str) -> None:
        session = await self._read(sid)
        await self._redis.delete(self._session_key(sid))
        if session is not None:
            await self._redis.srem(self._user_sessions_key(session.user_id), sid)

    async def revoke_all(self, user_id: uuid.UUID) -> None:
        reverse_key = self._user_sessions_key(user_id)
        sids = await self._redis.smembers(reverse_key)
        normalized_sids = [self._normalize_sid(sid) for sid in sids]
        if normalized_sids:
            await self._redis.delete(*(self._session_key(sid) for sid in normalized_sids))
        await self._redis.delete(reverse_key)

    async def list_for_user(self, user_id: uuid.UUID) -> list[SessionData]:
        reverse_key = self._user_sessions_key(user_id)
        sids = await self._redis.smembers(reverse_key)
        sessions: list[SessionData] = []
        for sid in sorted(self._normalize_sid(sid) for sid in sids):
            session = await self._read(sid)
            if session is None or self._is_absolute_expired(session):
                await self.revoke(sid)
                continue
            sessions.append(session)
        return sessions

    async def set_session_version(self, sid: str, session_version: int) -> bool:
        session = await self._read(sid)
        if session is None or self._is_absolute_expired(session):
            await self.revoke(sid)
            return False

        updated = SessionData(
            sid=session.sid,
            user_id=session.user_id,
            session_version=session_version,
            created_at=session.created_at,
            absolute_expiry=session.absolute_expiry,
            last_seen=self._now(),
            ip=session.ip,
        )
        await self._redis.set(self._session_key(sid), self._dump(updated), ex=self._idle_seconds)
        return True

    async def _read(self, sid: str) -> SessionData | None:
        raw = await self._redis.get(self._session_key(sid))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return self._load(sid, raw)

    def _is_absolute_expired(self, session: SessionData) -> bool:
        return self._now() >= session.absolute_expiry

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            return now.replace(tzinfo=UTC)
        return now

    @staticmethod
    def _default_clock() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _session_key(sid: str) -> str:
        return f"session:{sid}"

    @staticmethod
    def _user_sessions_key(user_id: uuid.UUID) -> str:
        return f"user_sessions:{user_id}"

    @staticmethod
    def _normalize_sid(sid: bytes | str) -> str:
        if isinstance(sid, bytes):
            return sid.decode()
        return sid

    @staticmethod
    def _dump(session: SessionData) -> str:
        payload: dict[str, Any] = {
            "user_id": str(session.user_id),
            "session_version": session.session_version,
            "created_at": session.created_at.isoformat(),
            "absolute_expiry": session.absolute_expiry.isoformat(),
            "last_seen": session.last_seen.isoformat(),
            "ip": session.ip,
        }
        return json.dumps(payload, separators=(",", ":"))

    @staticmethod
    def _load(sid: str, raw: str) -> SessionData:
        payload = json.loads(raw)
        return SessionData(
            sid=sid,
            user_id=uuid.UUID(payload["user_id"]),
            session_version=int(payload["session_version"]),
            created_at=datetime.fromisoformat(payload["created_at"]),
            absolute_expiry=datetime.fromisoformat(payload["absolute_expiry"]),
            last_seen=datetime.fromisoformat(payload["last_seen"]),
            ip=payload["ip"],
        )
