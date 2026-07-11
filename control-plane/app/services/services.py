import uuid
from dataclasses import dataclass
from decimal import Decimal
from ipaddress import IPv4Network
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import CIDR
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.applystate import assert_transition
from app.core.config import get_settings
from app.db.models import (
    ApplyStatus,
    ChangeTrigger,
    ProtectedService,
    Role,
    ServiceMode,
    ServicePlan,
    Tenant,
    User,
    utc_now,
)
from app.services.allocations import cidr_in_tenant_allocation
from app.services.apply import enqueue_service_update
from app.services.audit import record_event

DEFAULT_BILLING_METRIC = "p95_clean_bps"


class PrincipalLike(Protocol):
    @property
    def role(self) -> Role: ...

    @property
    def tenant_id(self) -> uuid.UUID | None: ...


@dataclass(frozen=True)
class ServiceRecord:
    service: ProtectedService
    plan: ServicePlan
    tenant: Tenant
    creator: User | None


@dataclass(frozen=True)
class PlanSizingResult:
    service: ProtectedService
    plan: ServicePlan
    warnings: list[str]


async def create_service(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    cidr_or_ip: IPv4Network,
    actor: User | None,
    mode: ServiceMode = ServiceMode.allow_rule_only,
    vip_pps: int | None = None,
    vip_bps: int | None = None,
    committed_clean_gbps: Decimal | None = None,
    ceiling_clean_gbps: Decimal | None = None,
    billing_metric: str = DEFAULT_BILLING_METRIC,
    ip: str | None = None,
) -> ServiceRecord:
    _authorize_actor_for_tenant(actor, tenant_id)
    if actor is not None and actor.role == Role.tenant_user:
        if committed_clean_gbps is not None or ceiling_clean_gbps is not None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    committed = committed_clean_gbps if committed_clean_gbps is not None else Decimal("0")
    ceiling = ceiling_clean_gbps if ceiling_clean_gbps is not None else Decimal("0")
    _validate_plan(committed, ceiling)
    await _require_destination_scope(db, tenant_id, cidr_or_ip)
    conflict = await _find_conflicting_service(db, cidr_or_ip)
    if conflict is not None:
        raise _overlap_error(conflict)

    dp_id = (await db.execute(select(func.nextval("service_dp_id_seq")))).scalar_one()
    service = ProtectedService(
        dp_id=dp_id,
        tenant_id=tenant_id,
        name=name,
        cidr_or_ip=str(cidr_or_ip),
        mode=mode,
        enabled=False,
        vip_pps=vip_pps,
        vip_bps=vip_bps,
        apply_status=ApplyStatus.pending,
        version=1,
        created_by=actor.id if actor is not None else None,
    )
    plan = ServicePlan(
        service=service,
        committed_clean_gbps=committed,
        ceiling_clean_gbps=ceiling,
        billing_metric=billing_metric,
    )
    db.add_all([service, plan])
    try:
        await db.flush()
    except IntegrityError as exc:
        _raise_integrity_error(exc)

    await record_event(
        db,
        actor=actor,
        action="service.create",
        target_type="protected_service",
        target_id=str(service.id),
        outcome="success",
        ip=ip,
        metadata={"tenant_id": str(tenant_id), "cidr_or_ip": str(cidr_or_ip), "name": name},
    )
    await enqueue_service_update(db, service, actor, ChangeTrigger.service)
    return await _record_for_service(db, service)


async def list_services(db: AsyncSession, principal: PrincipalLike) -> list[ServiceRecord]:
    statement = select(ProtectedService).options(
        selectinload(ProtectedService.plan),
        selectinload(ProtectedService.tenant),
        selectinload(ProtectedService.creator),
    )
    if principal.role != Role.admin:
        if principal.tenant_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        statement = statement.where(ProtectedService.tenant_id == principal.tenant_id)
    services = (await db.execute(statement.order_by(ProtectedService.created_at))).scalars().all()
    return [_record_from_loaded(service) for service in services]


async def get_service(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    principal: PrincipalLike,
) -> ServiceRecord:
    service = await _load_service(db, service_id)
    if service is None or not _principal_can_access(principal, service.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return _record_from_loaded(service)


async def update_service(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    actor: User | None,
    name: str | None = None,
    cidr_or_ip: IPv4Network | None = None,
    mode: ServiceMode | None = None,
    vip_pps: int | None = None,
    vip_bps: int | None = None,
    ip: str | None = None,
) -> ServiceRecord:
    service = await _require_service(db, service_id)
    _authorize_actor_for_tenant(actor, service.tenant_id)

    if cidr_or_ip is not None and str(cidr_or_ip) != str(service.cidr_or_ip):
        await _require_destination_scope(db, service.tenant_id, cidr_or_ip)
        conflict = await _find_conflicting_service(db, cidr_or_ip, exclude_service_id=service.id)
        if conflict is not None:
            raise _overlap_error(conflict)
        service.cidr_or_ip = str(cidr_or_ip)
    if name is not None:
        service.name = name
    if mode is not None:
        service.mode = mode
    if vip_pps is not None:
        service.vip_pps = vip_pps
    if vip_bps is not None:
        service.vip_bps = vip_bps

    service = await bump_version(db, service.id)
    await enqueue_service_update(db, service, actor, ChangeTrigger.service)
    await record_event(
        db,
        actor=actor,
        action="service.update",
        target_type="protected_service",
        target_id=str(service.id),
        outcome="success",
        ip=ip,
        metadata={"tenant_id": str(service.tenant_id)},
    )
    return await _record_for_service(db, service)


async def set_enabled(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    enabled: bool,
    actor: User | None,
    ip: str | None = None,
) -> ProtectedService:
    service = await _require_service(db, service_id)
    _authorize_actor_for_tenant(actor, service.tenant_id)
    if service.enabled == enabled:
        return service

    service.enabled = enabled
    trigger = ChangeTrigger.enable if enabled else ChangeTrigger.disable
    service = await bump_version(db, service.id)
    await enqueue_service_update(db, service, actor, trigger)
    await record_event(
        db,
        actor=actor,
        action="service.enable" if enabled else "service.disable",
        target_type="protected_service",
        target_id=str(service.id),
        outcome="success",
        ip=ip,
        metadata={"dangerous": not enabled},
    )
    return service


async def size_plan(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    actor: User | None,
    committed_clean_gbps: Decimal,
    ceiling_clean_gbps: Decimal,
    ip: str | None = None,
) -> PlanSizingResult:
    _require_admin_actor(actor)
    _validate_plan(committed_clean_gbps, ceiling_clean_gbps)
    service = await _require_service(db, service_id)
    plan = await _require_plan(db, service.id)
    plan.committed_clean_gbps = committed_clean_gbps
    plan.ceiling_clean_gbps = ceiling_clean_gbps
    service = await bump_version(db, service.id)
    await enqueue_service_update(db, service, actor, ChangeTrigger.plan)
    await record_event(
        db,
        actor=actor,
        action="service.plan.update",
        target_type="protected_service",
        target_id=str(service.id),
        outcome="success",
        ip=ip,
        metadata={
            "committed_clean_gbps": str(committed_clean_gbps),
            "ceiling_clean_gbps": str(ceiling_clean_gbps),
        },
    )
    warnings = await _oversubscription_warnings(db)
    return PlanSizingResult(service=service, plan=plan, warnings=warnings)


async def delete_service(
    db: AsyncSession,
    *,
    service_id: uuid.UUID,
    actor: User | None,
    ip: str | None = None,
) -> None:
    service = await _require_service(db, service_id)
    _authorize_actor_for_tenant(actor, service.tenant_id)
    if service.enabled:
        await record_event(
            db,
            actor=actor,
            action="service.delete",
            target_type="protected_service",
            target_id=str(service.id),
            outcome="denied",
            ip=ip,
            metadata={"reason": "disable first", "dangerous": True},
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Disable service first")

    await record_event(
        db,
        actor=actor,
        action="service.delete",
        target_type="protected_service",
        target_id=str(service.id),
        outcome="success",
        ip=ip,
        metadata={"dangerous": True, "tenant_id": str(service.tenant_id)},
    )
    await db.delete(service)
    await db.flush()


async def bump_version(db: AsyncSession, service_id: uuid.UUID) -> ProtectedService:
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
    service.version += 1
    assert_transition(service.apply_status, ApplyStatus.pending)
    service.apply_status = ApplyStatus.pending
    service.updated_at = utc_now()
    await db.flush()
    return service


async def services_in_cidr(db: AsyncSession, cidr: IPv4Network) -> list[ProtectedService]:
    return list(
        (
            await db.execute(
                select(ProtectedService)
                .where(ProtectedService.cidr_or_ip.op("<<=")(_cidr_value(cidr)))
                .order_by(ProtectedService.created_at)
            )
        )
        .scalars()
        .all()
    )


async def _load_service(db: AsyncSession, service_id: uuid.UUID) -> ProtectedService | None:
    return (
        (
            await db.execute(
                select(ProtectedService)
                .options(
                    selectinload(ProtectedService.plan),
                    selectinload(ProtectedService.tenant),
                    selectinload(ProtectedService.creator),
                )
                .where(ProtectedService.id == service_id)
            )
        )
        .scalars()
        .one_or_none()
    )


async def _require_service(db: AsyncSession, service_id: uuid.UUID) -> ProtectedService:
    service = await _load_service(db, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return service


async def _require_plan(db: AsyncSession, service_id: uuid.UUID) -> ServicePlan:
    plan = (
        (await db.execute(select(ServicePlan).where(ServicePlan.service_id == service_id)))
        .scalars()
        .one_or_none()
    )
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service plan not found")
    return plan


async def _record_for_service(db: AsyncSession, service: ProtectedService) -> ServiceRecord:
    loaded = await _load_service(db, service.id)
    if loaded is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return _record_from_loaded(loaded)


def _record_from_loaded(service: ProtectedService) -> ServiceRecord:
    if service.plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service plan not found")
    return ServiceRecord(
        service=service,
        plan=service.plan,
        tenant=service.tenant,
        creator=service.creator,
    )


async def _require_destination_scope(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cidr_or_ip: IPv4Network,
) -> None:
    if not await cidr_in_tenant_allocation(db, tenant_id, cidr_or_ip):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


async def _find_conflicting_service(
    db: AsyncSession,
    cidr_or_ip: IPv4Network,
    *,
    exclude_service_id: uuid.UUID | None = None,
) -> ProtectedService | None:
    statement = select(ProtectedService).where(
        ProtectedService.cidr_or_ip.op("&&")(_cidr_value(cidr_or_ip))
    )
    if exclude_service_id is not None:
        statement = statement.where(ProtectedService.id != exclude_service_id)
    return (await db.execute(statement.order_by(ProtectedService.created_at))).scalars().first()


def _authorize_actor_for_tenant(actor: User | None, tenant_id: uuid.UUID) -> None:
    if actor is None or actor.role == Role.admin:
        return
    if actor.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _require_admin_actor(actor: User | None) -> None:
    if actor is None or actor.role != Role.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _principal_can_access(principal: PrincipalLike, tenant_id: uuid.UUID) -> bool:
    return principal.role == Role.admin or principal.tenant_id == tenant_id


def _validate_plan(committed: Decimal, ceiling: Decimal) -> None:
    if committed < 0 or ceiling < 0 or committed > ceiling:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="committed_clean_gbps must be between 0 and ceiling_clean_gbps",
        )


async def _oversubscription_warnings(db: AsyncSession) -> list[str]:
    total = (
        await db.execute(
            select(func.coalesce(func.sum(ServicePlan.committed_clean_gbps), Decimal("0")))
            .join(ProtectedService, ProtectedService.id == ServicePlan.service_id)
            .where(ProtectedService.enabled.is_(True))
        )
    ).scalar_one()
    capacity = get_settings().node_clean_capacity_gbps
    if total <= capacity:
        return []
    return [
        f"Committed clean bandwidth {total:.2f} exceeds node capacity {capacity:.2f}",
    ]


def _raise_integrity_error(exc: IntegrityError) -> None:
    detail = str(exc)
    if "protected_service_dest_no_overlap" in detail:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Service destination overlaps another service",
        ) from exc
    if "ck_service_plan_committed_le_ceiling" in detail:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="committed_clean_gbps must be between 0 and ceiling_clean_gbps",
        ) from exc
    raise exc


def _overlap_error(conflict: ProtectedService) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Service destination overlaps {conflict.name}",
    )


def _cidr_value(cidr: IPv4Network) -> object:
    return cast(str(cidr), CIDR)
