import uuid
from dataclasses import dataclass
from ipaddress import IPv4Network

from fastapi import HTTPException, status
from sqlalchemy import cast, exists, select
from sqlalchemy.dialects.postgresql import CIDR
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AllocatedCIDR, CIDRStatus, Tenant, TenantStatus, User, utc_now
from app.services.audit import record_event


@dataclass(frozen=True)
class AllocationUsage:
    allocation: AllocatedCIDR
    dependent_count: int


@dataclass(frozen=True)
class OverlapCheckResult:
    overlaps: bool
    conflicts: list[AllocatedCIDR]


async def allocate(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cidr: IPv4Network,
    actor: User | None,
    ip: str | None = None,
) -> AllocatedCIDR:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    if tenant.status != TenantStatus.active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tenant is not active")

    conflict = await _find_conflict(db, cidr)
    if conflict is not None:
        raise _overlap_error(conflict)

    allocation = AllocatedCIDR(
        tenant_id=tenant.id,
        cidr=str(cidr),
        status=CIDRStatus.active,
        allocated_by=actor.id if actor is not None else None,
    )
    db.add(allocation)
    try:
        await db.flush()
    except IntegrityError as exc:
        if "allocated_cidr_active_no_overlap" not in str(exc):
            raise
        await db.rollback()
        conflict = await _find_conflict(db, cidr)
        if conflict is not None:
            raise _overlap_error(conflict) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="CIDR overlaps an active allocation",
        ) from exc

    await record_event(
        db,
        actor=actor,
        action="allocation.allocate",
        target_type="allocated_cidr",
        target_id=str(allocation.id),
        outcome="success",
        ip=ip,
        metadata={"tenant_id": str(tenant.id), "cidr": str(cidr)},
    )
    return allocation


async def revoke(
    db: AsyncSession,
    *,
    allocation_id: uuid.UUID,
    actor: User | None,
    ip: str | None = None,
) -> AllocatedCIDR:
    allocation = await db.get(AllocatedCIDR, allocation_id)
    if allocation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Allocation not found")
    if allocation.status == CIDRStatus.revoked:
        return allocation

    blockers = await count_allocation_dependents(db, allocation)
    if blockers:
        await record_event(
            db,
            actor=actor,
            action="allocation.revoke",
            target_type="allocated_cidr",
            target_id=str(allocation.id),
            outcome="denied",
            ip=ip,
            metadata={"blockers": blockers},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Allocation is still in use", "blockers": blockers},
        )

    allocation.status = CIDRStatus.revoked
    allocation.updated_at = utc_now()
    await db.flush()
    await record_event(
        db,
        actor=actor,
        action="allocation.revoke",
        target_type="allocated_cidr",
        target_id=str(allocation.id),
        outcome="success",
        ip=ip,
        metadata={"tenant_id": str(allocation.tenant_id), "cidr": str(allocation.cidr)},
    )
    return allocation


async def list_for_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> list[AllocationUsage]:
    allocations = (
        await db.execute(
            select(AllocatedCIDR)
            .where(AllocatedCIDR.tenant_id == tenant_id)
            .order_by(AllocatedCIDR.created_at)
        )
    ).scalars()
    return [
        AllocationUsage(
            allocation=allocation,
            dependent_count=len(await count_allocation_dependents(db, allocation)),
        )
        for allocation in allocations
    ]


async def overlap_check(db: AsyncSession, candidate: IPv4Network) -> OverlapCheckResult:
    conflicts = (
        (
            await db.execute(
                select(AllocatedCIDR)
                .where(AllocatedCIDR.status == CIDRStatus.active)
                .where(AllocatedCIDR.cidr.op("&&")(_cidr_value(candidate)))
                .order_by(AllocatedCIDR.created_at)
            )
        )
        .scalars()
        .all()
    )
    return OverlapCheckResult(overlaps=bool(conflicts), conflicts=list(conflicts))


async def cidr_in_tenant_allocation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    target: IPv4Network,
) -> bool:
    result = (
        await db.execute(
            select(
                exists().where(
                    AllocatedCIDR.tenant_id == tenant_id,
                    AllocatedCIDR.status == CIDRStatus.active,
                    AllocatedCIDR.cidr.op(">>=")(_cidr_value(target)),
                )
            )
        )
    ).scalar_one_or_none()
    return bool(result)


async def count_allocation_dependents(
    _db: AsyncSession,
    _allocation: AllocatedCIDR,
) -> list[str]:
    return []


async def _find_conflict(db: AsyncSession, cidr: IPv4Network) -> AllocatedCIDR | None:
    return (
        (
            await db.execute(
                select(AllocatedCIDR)
                .where(AllocatedCIDR.status == CIDRStatus.active)
                .where(AllocatedCIDR.cidr.op("&&")(_cidr_value(cidr)))
                .order_by(AllocatedCIDR.created_at)
            )
        )
        .scalars()
        .first()
    )


def _cidr_value(cidr: IPv4Network) -> object:
    return cast(str(cidr), CIDR)


def _overlap_error(conflict: AllocatedCIDR) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"CIDR overlaps active allocation {conflict.cidr}",
    )
