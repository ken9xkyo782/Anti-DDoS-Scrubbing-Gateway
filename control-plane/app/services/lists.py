import uuid
from ipaddress import IPv4Network

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cidr import CidrValidationError, parse_ipv4_cidr, reject_reserved
from app.db.models import (
    BlacklistEntry,
    BlacklistScope,
    BlacklistSource,
    ChangeTrigger,
    FeedBlacklistAssertion,
    ProtectedService,
    Role,
    User,
    WhitelistEntry,
)
from app.services.apply import enqueue_service_update
from app.services.audit import record_event
from app.services.feed_reconcile import materialize_global_union
from app.services.services import bump_version
from app.worker.feed_scheduler import _enqueue_global_convergence


async def add_whitelist(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    source_cidr: str,
    actor: User | None,
    ip: str | None = None,
) -> WhitelistEntry:
    service = await _require_service(db, service_id)
    _authorize_actor(actor, service)
    source = _parse_source(source_cidr)
    entry = WhitelistEntry(
        service_id=service.id,
        source_cidr=str(source),
        created_by=actor.id if actor is not None else None,
    )
    db.add(entry)
    try:
        await db.flush()
    except IntegrityError as exc:
        _raise_integrity_error(exc)
    service = await bump_version(db, service.id)
    await enqueue_service_update(db, service, actor, ChangeTrigger.whitelist)
    await record_event(
        db,
        actor=actor,
        action="list.whitelist.add",
        target_type="whitelist_entry",
        target_id=str(entry.id),
        outcome="success",
        ip=ip,
        metadata={"service_id": str(service.id), "source_cidr": str(source)},
    )
    return entry


async def remove_whitelist(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    source_cidr: str,
    actor: User | None,
    ip: str | None = None,
) -> ProtectedService:
    service = await _require_service(db, service_id)
    _authorize_actor(actor, service)
    source = _parse_source(source_cidr)
    entry = await _require_whitelist_entry(db, service.id, source)
    await db.delete(entry)
    await db.flush()
    service = await bump_version(db, service.id)
    await enqueue_service_update(db, service, actor, ChangeTrigger.whitelist)
    await record_event(
        db,
        actor=actor,
        action="list.whitelist.remove",
        target_type="whitelist_entry",
        target_id=str(entry.id),
        outcome="success",
        ip=ip,
        metadata={"service_id": str(service.id), "source_cidr": str(source)},
    )
    return service


async def list_whitelist(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    actor: User | None,
) -> list[WhitelistEntry]:
    service = await _require_service(db, service_id)
    _authorize_actor(actor, service)
    return list(
        (
            await db.execute(
                select(WhitelistEntry)
                .where(WhitelistEntry.service_id == service.id)
                .order_by(WhitelistEntry.created_at)
            )
        )
        .scalars()
        .all()
    )


async def add_blacklist(
    db: AsyncSession,
    *,
    scope: BlacklistScope,
    service_id: uuid.UUID | None,
    source_cidr: str,
    actor: User | None,
    ip: str | None = None,
) -> BlacklistEntry:
    source = _parse_source(source_cidr)
    service: ProtectedService | None = None
    if scope == BlacklistScope.service:
        if service_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
        service = await _require_service(db, service_id)
        _authorize_actor(actor, service)
    else:
        _require_admin_actor(actor)

    if scope == BlacklistScope.global_:
        entry = await _global_blacklist_entry_for_update(db, source)
        if entry is None:
            entry = BlacklistEntry(
                service_id=None,
                scope=scope,
                source=BlacklistSource.manual,
                source_cidr=str(source),
                created_by=actor.id if actor is not None else None,
            )
            db.add(entry)
            try:
                await db.flush()
            except IntegrityError as exc:
                _raise_integrity_error(exc)
        elif entry.source == BlacklistSource.feed:
            entry.source = BlacklistSource.manual
            entry.created_by = actor.id if actor is not None else None
            await db.flush()
        else:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="List entry exists")
        await _materialize_global_deny_and_enqueue(db)
    else:
        entry = BlacklistEntry(
            service_id=service.id if service is not None else None,
            scope=scope,
            source=BlacklistSource.manual,
            source_cidr=str(source),
            created_by=actor.id if actor is not None else None,
        )
        db.add(entry)
        try:
            await db.flush()
        except IntegrityError as exc:
            _raise_integrity_error(exc)
    if service is not None:
        service = await bump_version(db, service.id)
        await enqueue_service_update(db, service, actor, ChangeTrigger.blacklist)
    await record_event(
        db,
        actor=actor,
        action="list.blacklist.add",
        target_type="blacklist_entry",
        target_id=str(entry.id),
        outcome="success",
        ip=ip,
        metadata={
            "scope": scope.value,
            "service_id": str(service.id) if service is not None else None,
            "source_cidr": str(source),
        },
    )
    return entry


async def remove_blacklist(
    db: AsyncSession,
    *,
    scope: BlacklistScope,
    service_id: uuid.UUID | None,
    source_cidr: str,
    actor: User | None,
    ip: str | None = None,
) -> ProtectedService | None:
    source = _parse_source(source_cidr)
    service: ProtectedService | None = None
    if scope == BlacklistScope.service:
        if service_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
        service = await _require_service(db, service_id)
        _authorize_actor(actor, service)
    else:
        _require_admin_actor(actor)

    entry = await _require_blacklist_entry(db, scope=scope, service_id=service_id, source=source)
    if scope == BlacklistScope.global_:
        if entry.source == BlacklistSource.feed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Feed-managed blacklist entry cannot be deleted manually",
            )
        if await _has_feed_assertions(db, entry.id):
            entry.source = BlacklistSource.feed
            entry.created_by = None
        else:
            await db.delete(entry)
        await db.flush()
        await _materialize_global_deny_and_enqueue(db)
    else:
        await db.delete(entry)
        await db.flush()
    if service is not None:
        service = await bump_version(db, service.id)
        await enqueue_service_update(db, service, actor, ChangeTrigger.blacklist)
    await record_event(
        db,
        actor=actor,
        action="list.blacklist.remove",
        target_type="blacklist_entry",
        target_id=str(entry.id),
        outcome="success",
        ip=ip,
        metadata={
            "scope": scope.value,
            "service_id": str(service.id) if service is not None else None,
            "source_cidr": str(source),
        },
    )
    return service


async def list_blacklist(
    db: AsyncSession,
    *,
    scope: BlacklistScope,
    service_id: uuid.UUID | None,
    actor: User | None,
) -> list[BlacklistEntry]:
    if scope == BlacklistScope.service:
        if service_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
        service = await _require_service(db, service_id)
        _authorize_actor(actor, service)
        statement = select(BlacklistEntry).where(
            BlacklistEntry.scope == BlacklistScope.service,
            BlacklistEntry.service_id == service.id,
        )
    else:
        _require_admin_actor(actor)
        statement = select(BlacklistEntry).where(BlacklistEntry.scope == BlacklistScope.global_)

    return list((await db.execute(statement.order_by(BlacklistEntry.created_at))).scalars().all())


async def _require_service(db: AsyncSession, service_id: uuid.UUID) -> ProtectedService:
    service = await db.get(ProtectedService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return service


async def _require_whitelist_entry(
    db: AsyncSession,
    service_id: uuid.UUID,
    source: IPv4Network,
) -> WhitelistEntry:
    entry = (
        (
            await db.execute(
                select(WhitelistEntry).where(
                    WhitelistEntry.service_id == service_id,
                    WhitelistEntry.source_cidr == str(source),
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Whitelist entry not found",
        )
    return entry


async def _require_blacklist_entry(
    db: AsyncSession,
    *,
    scope: BlacklistScope,
    service_id: uuid.UUID | None,
    source: IPv4Network,
) -> BlacklistEntry:
    statement = select(BlacklistEntry).where(
        BlacklistEntry.scope == scope,
        BlacklistEntry.source_cidr == str(source),
    )
    if scope == BlacklistScope.service:
        statement = statement.where(BlacklistEntry.service_id == service_id)
    else:
        statement = statement.where(BlacklistEntry.service_id.is_(None))
    entry = (await db.execute(statement)).scalars().one_or_none()
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Blacklist entry not found",
        )
    return entry


async def _global_blacklist_entry_for_update(
    db: AsyncSession,
    source: IPv4Network,
) -> BlacklistEntry | None:
    return (
        (
            await db.execute(
                select(BlacklistEntry)
                .where(
                    BlacklistEntry.scope == BlacklistScope.global_,
                    BlacklistEntry.service_id.is_(None),
                    BlacklistEntry.source_cidr == str(source),
                )
                .with_for_update()
            )
        )
        .scalars()
        .one_or_none()
    )


async def _has_feed_assertions(db: AsyncSession, entry_id: uuid.UUID) -> bool:
    return (
        await db.execute(
            select(FeedBlacklistAssertion.feed_source_id)
            .where(FeedBlacklistAssertion.blacklist_entry_id == entry_id)
            .limit(1)
        )
    ).scalar_one_or_none() is not None


async def _materialize_global_deny_and_enqueue(db: AsyncSession) -> None:
    materialized = await materialize_global_union(db)
    if materialized.changed:
        await _enqueue_global_convergence(db)


def _parse_source(value: str) -> IPv4Network:
    try:
        source = parse_ipv4_cidr(value)
        reject_reserved(source)
    except CidrValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return source


def _authorize_actor(actor: User | None, service: ProtectedService) -> None:
    if actor is None or actor.role == Role.admin:
        return
    if actor.tenant_id != service.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _require_admin_actor(actor: User | None) -> None:
    if actor is None or actor.role != Role.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _raise_integrity_error(exc: IntegrityError) -> None:
    detail = str(exc)
    if (
        "uq_whitelist_service_source_cidr" in detail
        or "uq_blacklist_service_source_cidr" in detail
        or "uq_blacklist_global_source_cidr" in detail
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="List entry exists",
        ) from exc
    raise exc
