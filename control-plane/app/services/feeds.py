import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.feed_parser import parse_line_list
from app.db.models import (
    AgentJob,
    ChangeTrigger,
    FeedFormat,
    FeedSyncRun,
    FeedSyncStatus,
    JobStatus,
    JobType,
    ThreatFeedSource,
    User,
    utc_now,
)
from app.db.session import add_post_commit_callback
from app.services.apply import ApplyDispatcher
from app.services.audit import record_event
from app.services.feed_reconcile import reconcile

MIN_SYNC_INTERVAL_SECONDS = 300
MAX_SYNC_INTERVAL_SECONDS = 604800
FEED_SYNC_TARGET_TYPE = "feed_sync_run"
_CREDENTIAL_ENV_VAR = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_DISPATCH_JOB_IDS_KEY = "feed_dispatch_job_ids"
_MISSING = object()


@dataclass(frozen=True)
class FeedSourceRecord:
    id: uuid.UUID
    name: str
    url: str
    format: FeedFormat
    enabled: bool
    sync_interval_seconds: int
    has_credential: bool
    last_status: FeedSyncStatus | None
    last_error: str | None
    last_sync_at: datetime | None
    next_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime


async def create_source(
    db: AsyncSession,
    payload: Mapping[str, Any] | object,
    actor: User | None,
) -> ThreatFeedSource:
    name = _validate_name(_required(payload, "name"))
    url = _validate_url(_required(payload, "url"))
    interval = _validate_interval(_required(payload, "sync_interval_seconds"))
    feed_format = _validate_format(_value(payload, "format", FeedFormat.line_list))
    credential_env_var = _validate_credential_env_var(_value(payload, "credential_env_var", None))
    enabled = _validate_enabled(_value(payload, "enabled", True))

    await _ensure_name_available(db, name)
    now = utc_now()
    source = ThreatFeedSource(
        name=name,
        url=url,
        format=feed_format,
        enabled=enabled,
        sync_interval_seconds=interval,
        credential_env_var=credential_env_var,
        next_sync_at=now if enabled else None,
    )
    try:
        async with db.begin_nested():
            db.add(source)
            await db.flush()
    except IntegrityError as exc:
        if _is_name_conflict(exc):
            raise _conflict("Feed source name already exists") from exc
        raise

    await record_event(
        db,
        actor=actor,
        action="feed.create",
        target_type="threat_feed_source",
        target_id=str(source.id),
        outcome="success",
        metadata={"name": source.name, "enabled": source.enabled},
    )
    return source


async def update_source(
    db: AsyncSession,
    source: ThreatFeedSource,
    payload: Mapping[str, Any] | object,
    actor: User | None,
) -> ThreatFeedSource:
    locked = await _active_source_for_update(db, source.id)
    previous_enabled = locked.enabled
    url_changed = False
    credential_changed = False
    interval_changed = False

    if _has(payload, "name"):
        name = _validate_name(_value(payload, "name"))
        if name.casefold() != locked.name.casefold():
            await _ensure_name_available(db, name, exclude_source_id=locked.id)
            locked.name = name
    if _has(payload, "url"):
        url = _validate_url(_value(payload, "url"))
        url_changed = url != locked.url
        locked.url = url
    if _has(payload, "format"):
        locked.format = _validate_format(_value(payload, "format"))
    if _has(payload, "credential_env_var"):
        credential_env_var = _validate_credential_env_var(_value(payload, "credential_env_var"))
        credential_changed = credential_env_var != locked.credential_env_var
        locked.credential_env_var = credential_env_var
    if _has(payload, "sync_interval_seconds"):
        interval = _validate_interval(_value(payload, "sync_interval_seconds"))
        interval_changed = interval != locked.sync_interval_seconds
        locked.sync_interval_seconds = interval
    if _has(payload, "enabled"):
        locked.enabled = _validate_enabled(_value(payload, "enabled"))

    now = utc_now()
    if not locked.enabled:
        locked.next_sync_at = None
    elif not previous_enabled or url_changed or credential_changed:
        locked.next_sync_at = now
    elif interval_changed:
        locked.next_sync_at = now + timedelta(seconds=locked.sync_interval_seconds)

    locked.updated_at = now
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_name_conflict(exc):
            raise _conflict("Feed source name already exists") from exc
        raise

    await record_event(
        db,
        actor=actor,
        action="feed.update",
        target_type="threat_feed_source",
        target_id=str(locked.id),
        outcome="success",
        metadata={"name": locked.name, "enabled": locked.enabled},
    )
    return locked


async def delete_source(
    db: AsyncSession,
    source: ThreatFeedSource,
    actor: User | None,
) -> FeedSyncRun:
    locked = await _active_source_for_update(db, source.id)
    now = utc_now()
    locked.enabled = False
    locked.next_sync_at = None
    locked.deleted_at = now
    locked.updated_at = now
    await db.flush()

    run = await _enqueue_locked(
        db,
        locked,
        trigger=ChangeTrigger.feed_delete,
        dry_run=False,
    )
    if run.trigger != ChangeTrigger.feed_delete:
        run.trigger = ChangeTrigger.feed_delete
        run.dry_run = False
        job = await _job_for_run_for_update(db, run.id)
        if job is not None:
            job.trigger = ChangeTrigger.feed_delete
        await db.flush()
    await reconcile(db, run, parse_line_list(b""))
    await record_event(
        db,
        actor=actor,
        action="feed.delete",
        target_type="threat_feed_source",
        target_id=str(locked.id),
        outcome="success",
        metadata={"dangerous": True, "name": locked.name},
    )
    return run


async def enqueue_sync(
    db: AsyncSession,
    source: ThreatFeedSource,
    *,
    trigger: ChangeTrigger,
    dry_run: bool,
    actor: User | None,
) -> FeedSyncRun:
    del actor
    locked = await _source_for_update(db, source.id)
    if locked is None or (locked.deleted_at is not None and trigger != ChangeTrigger.feed_delete):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feed source not found")
    return await _enqueue_locked(db, locked, trigger=trigger, dry_run=dry_run)


async def list_due_sources(
    db: AsyncSession,
    now: datetime,
    limit: int,
) -> list[ThreatFeedSource]:
    if limit <= 0:
        return []

    inflight_source_ids = (
        select(FeedSyncRun.feed_source_id)
        .join(AgentJob, AgentJob.feed_sync_run_id == FeedSyncRun.id)
        .where(
            AgentJob.job_type == JobType.feed_sync,
            AgentJob.status.in_((JobStatus.queued, JobStatus.applying)),
        )
    )
    statement = (
        select(ThreatFeedSource)
        .where(
            ThreatFeedSource.enabled.is_(True),
            ThreatFeedSource.deleted_at.is_(None),
            ThreatFeedSource.next_sync_at.is_not(None),
            ThreatFeedSource.next_sync_at <= now,
            ThreatFeedSource.id.not_in(inflight_source_ids),
        )
        .order_by(ThreatFeedSource.next_sync_at, ThreatFeedSource.created_at, ThreatFeedSource.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await db.execute(statement)).scalars().all())


def source_record(source: ThreatFeedSource) -> FeedSourceRecord:
    return FeedSourceRecord(
        id=source.id,
        name=source.name,
        url=source.url,
        format=source.format,
        enabled=source.enabled,
        sync_interval_seconds=source.sync_interval_seconds,
        has_credential=source.credential_env_var is not None,
        last_status=source.last_status,
        last_error=source.last_error,
        last_sync_at=source.last_sync_at,
        next_sync_at=source.next_sync_at,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


async def _enqueue_locked(
    db: AsyncSession,
    source: ThreatFeedSource,
    *,
    trigger: ChangeTrigger,
    dry_run: bool,
) -> FeedSyncRun:
    existing = await _inflight_run(db, source.id)
    if existing is not None:
        return existing

    source.sync_sequence += 1
    run = FeedSyncRun(
        feed_source_id=source.id,
        source_name=source.name,
        sequence=source.sync_sequence,
        trigger=trigger,
        dry_run=dry_run,
        status=FeedSyncStatus.queued,
    )
    db.add(run)
    await db.flush()
    job = AgentJob(
        target_type=FEED_SYNC_TARGET_TYPE,
        feed_sync_run_id=run.id,
        version=run.sequence,
        job_type=JobType.feed_sync,
        trigger=trigger,
        status=JobStatus.queued,
    )
    db.add(job)
    await db.flush()
    _register_dispatch(db, job.id)
    return run


async def _inflight_run(db: AsyncSession, source_id: uuid.UUID) -> FeedSyncRun | None:
    return (
        (
            await db.execute(
                select(FeedSyncRun)
                .join(AgentJob, AgentJob.feed_sync_run_id == FeedSyncRun.id)
                .where(
                    FeedSyncRun.feed_source_id == source_id,
                    AgentJob.job_type == JobType.feed_sync,
                    AgentJob.status.in_((JobStatus.queued, JobStatus.applying)),
                )
                .order_by(AgentJob.created_at, AgentJob.id)
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )


async def _job_for_run_for_update(db: AsyncSession, run_id: uuid.UUID) -> AgentJob | None:
    return (
        (
            await db.execute(
                select(AgentJob)
                .where(
                    AgentJob.feed_sync_run_id == run_id,
                    AgentJob.job_type == JobType.feed_sync,
                )
                .with_for_update()
            )
        )
        .scalars()
        .one_or_none()
    )


async def _active_source_for_update(db: AsyncSession, source_id: uuid.UUID) -> ThreatFeedSource:
    source = await _source_for_update(db, source_id)
    if source is None or source.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feed source not found")
    return source


async def _source_for_update(
    db: AsyncSession,
    source_id: uuid.UUID,
) -> ThreatFeedSource | None:
    return (
        (
            await db.execute(
                select(ThreatFeedSource).where(ThreatFeedSource.id == source_id).with_for_update()
            )
        )
        .scalars()
        .one_or_none()
    )


async def _ensure_name_available(
    db: AsyncSession,
    name: str,
    *,
    exclude_source_id: uuid.UUID | None = None,
) -> None:
    statement = select(ThreatFeedSource.id).where(
        ThreatFeedSource.name == name,
        ThreatFeedSource.deleted_at.is_(None),
    )
    if exclude_source_id is not None:
        statement = statement.where(ThreatFeedSource.id != exclude_source_id)
    if (await db.execute(statement)).scalar_one_or_none() is not None:
        raise _conflict("Feed source name already exists")


def _register_dispatch(db: AsyncSession, job_id: uuid.UUID) -> None:
    job_ids = db.info.setdefault(_DISPATCH_JOB_IDS_KEY, set())
    if job_id in job_ids:
        return
    job_ids.add(job_id)

    async def dispatch() -> None:
        job_ids.discard(job_id)
        await ApplyDispatcher().dispatch(job_id)

    add_post_commit_callback(db, dispatch)


def _required(payload: Mapping[str, Any] | object, field: str) -> Any:
    value = _value(payload, field, _MISSING)
    if value is _MISSING:
        raise _unprocessable(f"{field} is required")
    return value


def _value(payload: Mapping[str, Any] | object, field: str, default: Any = _MISSING) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(field, default)
    return getattr(payload, field, default)


def _has(payload: Mapping[str, Any] | object, field: str) -> bool:
    if isinstance(payload, Mapping):
        return field in payload
    fields_set = getattr(payload, "model_fields_set", getattr(payload, "__fields_set__", None))
    return field in fields_set if fields_set is not None else hasattr(payload, field)


def _validate_name(value: Any) -> str:
    if not isinstance(value, str):
        raise _unprocessable("Feed source name must be a string")
    name = value.strip()
    if not name or len(name) > 255:
        raise _unprocessable("Feed source name must be between 1 and 255 characters")
    return name


def _validate_url(value: Any) -> str:
    if not isinstance(value, str):
        raise _unprocessable("Feed source URL must be a string")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise _unprocessable("Feed source URL must be a valid HTTPS URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or "#" in value
    ):
        raise _unprocessable("Feed source URL must be HTTPS without userinfo or fragments")
    return value


def _validate_interval(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _unprocessable("sync_interval_seconds must be an integer")
    if not MIN_SYNC_INTERVAL_SECONDS <= value <= MAX_SYNC_INTERVAL_SECONDS:
        raise _unprocessable("sync_interval_seconds must be between 300 and 604800")
    return value


def _validate_format(value: Any) -> FeedFormat:
    try:
        feed_format = FeedFormat(value)
    except (TypeError, ValueError) as exc:
        raise _unprocessable("Unsupported feed format") from exc
    if feed_format != FeedFormat.line_list:
        raise _unprocessable("Unsupported feed format")
    return feed_format


def _validate_credential_env_var(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _CREDENTIAL_ENV_VAR.fullmatch(value) is None:
        raise _unprocessable("credential_env_var must be an uppercase environment variable name")
    return value


def _validate_enabled(value: Any) -> bool:
    if not isinstance(value, bool):
        raise _unprocessable("enabled must be a boolean")
    return value


def _is_name_conflict(error: IntegrityError) -> bool:
    return "uq_threat_feed_source_name" in str(error.orig)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
