import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rulematch import PortRangeError, RuleView, find_overlaps, validate_port_range
from app.db.models import AllowRule, ChangeTrigger, ProtectedService, Protocol, Role, User
from app.services.apply import enqueue_service_update
from app.services.audit import record_event
from app.services.services import bump_version

MAX_RULES_PER_SERVICE = 16


@dataclass(frozen=True)
class RuleMutationResult:
    rule: AllowRule
    warnings: list[str]
    service: ProtectedService


async def create_rule(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    actor: User | None,
    priority: int,
    protocol: Protocol,
    src_port_lo: int | None = None,
    src_port_hi: int | None = None,
    dst_port_lo: int | None = None,
    dst_port_hi: int | None = None,
    enabled: bool = True,
    ip: str | None = None,
) -> RuleMutationResult:
    service = await _require_service_locked(db, service_id)
    _authorize_actor(actor, service)
    if await _rule_count(db, service.id) >= MAX_RULES_PER_SERVICE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Rule limit reached")
    await _ensure_priority_available(db, service.id, priority)
    candidate = _rule_view(
        rule_id="candidate",
        priority=priority,
        protocol=protocol,
        src_port_lo=src_port_lo,
        src_port_hi=src_port_hi,
        dst_port_lo=dst_port_lo,
        dst_port_hi=dst_port_hi,
    )
    warnings = await _overlap_warnings(db, service.id, candidate)
    rule = AllowRule(
        service_id=service.id,
        priority=priority,
        protocol=protocol,
        src_port_lo=src_port_lo,
        src_port_hi=src_port_hi,
        dst_port_lo=dst_port_lo,
        dst_port_hi=dst_port_hi,
        enabled=enabled,
    )
    db.add(rule)
    try:
        await db.flush()
    except IntegrityError as exc:
        _raise_integrity_error(exc)
    service = await bump_version(db, service.id)
    await enqueue_service_update(db, service, actor, ChangeTrigger.rule)
    await record_event(
        db,
        actor=actor,
        action="rule.create",
        target_type="allow_rule",
        target_id=str(rule.id),
        outcome="success",
        ip=ip,
        metadata={"service_id": str(service.id), "priority": priority},
    )
    return RuleMutationResult(rule=rule, warnings=warnings, service=service)


async def list_rules(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    actor: User | None,
) -> list[AllowRule]:
    service = await _require_service(db, service_id)
    _authorize_actor(actor, service)
    return list(
        (
            await db.execute(
                select(AllowRule)
                .where(AllowRule.service_id == service.id)
                .order_by(AllowRule.priority)
            )
        )
        .scalars()
        .all()
    )


async def get_rule(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    rule_id: uuid.UUID,
    actor: User | None,
) -> AllowRule:
    service = await _require_service(db, service_id)
    _authorize_actor(actor, service)
    return await _require_rule(db, service.id, rule_id)


async def update_rule(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    rule_id: uuid.UUID,
    actor: User | None,
    priority: int | None = None,
    protocol: Protocol | None = None,
    src_port_lo: int | None = None,
    src_port_hi: int | None = None,
    dst_port_lo: int | None = None,
    dst_port_hi: int | None = None,
    enabled: bool | None = None,
    ip: str | None = None,
) -> RuleMutationResult:
    service = await _require_service_locked(db, service_id)
    _authorize_actor(actor, service)
    rule = await _require_rule(db, service.id, rule_id)
    next_priority = priority if priority is not None else rule.priority
    if next_priority != rule.priority:
        await _ensure_priority_available(db, service.id, next_priority, exclude_rule_id=rule.id)
    candidate = _rule_view(
        rule_id=str(rule.id),
        priority=next_priority,
        protocol=protocol if protocol is not None else rule.protocol,
        src_port_lo=src_port_lo if src_port_lo is not None else rule.src_port_lo,
        src_port_hi=src_port_hi if src_port_hi is not None else rule.src_port_hi,
        dst_port_lo=dst_port_lo if dst_port_lo is not None else rule.dst_port_lo,
        dst_port_hi=dst_port_hi if dst_port_hi is not None else rule.dst_port_hi,
    )
    warnings = await _overlap_warnings(db, service.id, candidate, exclude_rule_id=rule.id)

    rule.priority = next_priority
    if protocol is not None:
        rule.protocol = protocol
    if src_port_lo is not None:
        rule.src_port_lo = src_port_lo
    if src_port_hi is not None:
        rule.src_port_hi = src_port_hi
    if dst_port_lo is not None:
        rule.dst_port_lo = dst_port_lo
    if dst_port_hi is not None:
        rule.dst_port_hi = dst_port_hi
    if enabled is not None:
        rule.enabled = enabled

    try:
        await db.flush()
    except IntegrityError as exc:
        _raise_integrity_error(exc)
    service = await bump_version(db, service.id)
    await enqueue_service_update(db, service, actor, ChangeTrigger.rule)
    await record_event(
        db,
        actor=actor,
        action="rule.update",
        target_type="allow_rule",
        target_id=str(rule.id),
        outcome="success",
        ip=ip,
        metadata={"service_id": str(service.id), "priority": rule.priority},
    )
    return RuleMutationResult(rule=rule, warnings=warnings, service=service)


async def delete_rule(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    rule_id: uuid.UUID,
    actor: User | None,
    ip: str | None = None,
) -> ProtectedService:
    service = await _require_service_locked(db, service_id)
    _authorize_actor(actor, service)
    rule = await _require_rule(db, service.id, rule_id)
    await db.delete(rule)
    await db.flush()
    service = await bump_version(db, service.id)
    await enqueue_service_update(db, service, actor, ChangeTrigger.rule)
    await record_event(
        db,
        actor=actor,
        action="rule.delete",
        target_type="allow_rule",
        target_id=str(rule.id),
        outcome="success",
        ip=ip,
        metadata={"service_id": str(service.id), "priority": rule.priority},
    )
    return service


async def overlap_dry_run(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    actor: User | None,
    protocol: Protocol,
    src_port_lo: int | None = None,
    src_port_hi: int | None = None,
    dst_port_lo: int | None = None,
    dst_port_hi: int | None = None,
) -> list[str]:
    service = await _require_service(db, service_id)
    _authorize_actor(actor, service)
    candidate = _rule_view(
        rule_id="candidate",
        priority=-1,
        protocol=protocol,
        src_port_lo=src_port_lo,
        src_port_hi=src_port_hi,
        dst_port_lo=dst_port_lo,
        dst_port_hi=dst_port_hi,
    )
    return await _overlap_warnings(db, service.id, candidate)


async def _require_service(db: AsyncSession, service_id: uuid.UUID) -> ProtectedService:
    service = await db.get(ProtectedService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return service


async def _require_service_locked(db: AsyncSession, service_id: uuid.UUID) -> ProtectedService:
    service = (
        (
            await db.execute(
                select(ProtectedService).where(ProtectedService.id == service_id).with_for_update()
            )
        )
        .scalars()
        .one_or_none()
    )
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return service


async def _require_rule(
    db: AsyncSession,
    service_id: uuid.UUID,
    rule_id: uuid.UUID,
) -> AllowRule:
    rule = (
        (
            await db.execute(
                select(AllowRule).where(
                    AllowRule.id == rule_id,
                    AllowRule.service_id == service_id,
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return rule


async def _rule_count(db: AsyncSession, service_id: uuid.UUID) -> int:
    return (
        await db.execute(select(func.count(AllowRule.id)).where(AllowRule.service_id == service_id))
    ).scalar_one()


async def _ensure_priority_available(
    db: AsyncSession,
    service_id: uuid.UUID,
    priority: int,
    *,
    exclude_rule_id: uuid.UUID | None = None,
) -> None:
    statement = select(AllowRule.id).where(
        AllowRule.service_id == service_id,
        AllowRule.priority == priority,
    )
    if exclude_rule_id is not None:
        statement = statement.where(AllowRule.id != exclude_rule_id)
    if (await db.execute(statement)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Rule priority exists")


async def _overlap_warnings(
    db: AsyncSession,
    service_id: uuid.UUID,
    candidate: RuleView,
    *,
    exclude_rule_id: uuid.UUID | None = None,
) -> list[str]:
    statement = select(AllowRule).where(AllowRule.service_id == service_id)
    if exclude_rule_id is not None:
        statement = statement.where(AllowRule.id != exclude_rule_id)
    existing = (await db.execute(statement.order_by(AllowRule.priority))).scalars().all()
    overlaps = find_overlaps([_view_from_rule(rule) for rule in existing], candidate)
    return [f"Overlaps rule priority {view.id}" for view in overlaps]


def _view_from_rule(rule: AllowRule) -> RuleView:
    return _rule_view(
        rule_id=str(rule.priority),
        priority=rule.priority,
        protocol=rule.protocol,
        src_port_lo=rule.src_port_lo,
        src_port_hi=rule.src_port_hi,
        dst_port_lo=rule.dst_port_lo,
        dst_port_hi=rule.dst_port_hi,
    )


def _rule_view(
    *,
    rule_id: str,
    priority: int,
    protocol: Protocol,
    src_port_lo: int | None,
    src_port_hi: int | None,
    dst_port_lo: int | None,
    dst_port_hi: int | None,
) -> RuleView:
    try:
        validate_port_range(src_port_lo, src_port_hi)
        validate_port_range(dst_port_lo, dst_port_hi)
    except PortRangeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return RuleView(
        id=rule_id,
        protocol=protocol.value,
        src_port=(src_port_lo, src_port_hi),
        dst_port=(dst_port_lo, dst_port_hi),
    )


def _authorize_actor(actor: User | None, service: ProtectedService) -> None:
    if actor is None or actor.role == Role.admin:
        return
    if actor.tenant_id != service.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _raise_integrity_error(exc: IntegrityError) -> None:
    if "uq_allow_rule_service_priority" in str(exc):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Rule priority exists",
        ) from exc
    raise exc
