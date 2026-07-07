from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent, User

_SECRET_KEY_PARTS = ("password", "token", "secret", "credential")


def scrub_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}

    scrubbed: dict[str, Any] = {}
    for key, value in metadata.items():
        if _is_secret_key(key):
            continue
        scrubbed[key] = _scrub_value(value)
    return scrubbed


async def record_event(
    db: AsyncSession,
    *,
    actor: User | None,
    action: str,
    target_type: str | None,
    target_id: str | None,
    outcome: str,
    ip: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_user_id=actor.id if actor is not None else None,
        actor_username=actor.username if actor is not None else "system",
        action=action,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        ip_address=ip,
        metadata=scrub_metadata(metadata),
    )
    db.add(event)
    await db.flush()
    return event


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _scrub_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return scrub_metadata(value)
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    return value
