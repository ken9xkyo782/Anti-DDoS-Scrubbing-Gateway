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
    ProtectedService,
    Role,
    User,
    WhitelistEntry,
)
from app.services.audit import record_event
from app.services.services import bump_version


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
    await bump_version(db, service.id)
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
) -> None:
    service = await _require_service(db, service_id)
    _authorize_actor(actor, service)
    source = _parse_source(source_cidr)
    entry = await _require_whitelist_entry(db, service.id, source)
    await db.delete(entry)
    await db.flush()
    await bump_version(db, service.id)
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
        await bump_version(db, service.id)
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
) -> None:
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
    await db.delete(entry)
    await db.flush()
    if service is not None:
        await bump_version(db, service.id)
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
