import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AllocatedCIDR, CIDRStatus, Tenant, TenantStatus, User
from app.services.audit import record_event


@dataclass(frozen=True)
class TenantSummary:
    tenant: Tenant
    active_allocation_count: int
    user_count: int


async def create_tenant(
    db: AsyncSession,
    *,
    actor: User | None,
    name: str,
    ip: str | None = None,
) -> Tenant:
    await _ensure_unique_name(db, name)
    tenant = Tenant(name=name, status=TenantStatus.active)
    db.add(tenant)
    await db.flush()
    await record_event(
        db,
        actor=actor,
        action="tenant.create",
        target_type="tenant",
        target_id=str(tenant.id),
        outcome="success",
        ip=ip,
        metadata={"name": tenant.name, "status": tenant.status.value},
    )
    return tenant


async def list_tenants(db: AsyncSession) -> list[TenantSummary]:
    tenants = (await db.execute(select(Tenant).order_by(Tenant.created_at))).scalars().all()
    return [await _summary(db, tenant) for tenant in tenants]


async def get_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    return await db.get(Tenant, tenant_id)


async def get_tenant_summary(db: AsyncSession, tenant_id: uuid.UUID) -> TenantSummary | None:
    tenant = await get_tenant(db, tenant_id)
    if tenant is None:
        return None
    return await _summary(db, tenant)


async def update_tenant(
    db: AsyncSession,
    *,
    actor: User | None,
    tenant_id: uuid.UUID,
    name: str | None = None,
    status: TenantStatus | None = None,
    ip: str | None = None,
) -> Tenant:
    tenant = await _get_existing_tenant(db, tenant_id)
    if name is not None and name != tenant.name:
        await _ensure_unique_name(db, name, exclude_tenant_id=tenant.id)
        tenant.name = name
    if status is not None:
        tenant.status = status

    await db.flush()
    await record_event(
        db,
        actor=actor,
        action="tenant.update",
        target_type="tenant",
        target_id=str(tenant.id),
        outcome="success",
        ip=ip,
        metadata={"name": tenant.name, "status": tenant.status.value},
    )
    return tenant


async def set_status(
    db: AsyncSession,
    *,
    actor: User | None,
    tenant_id: uuid.UUID,
    status: TenantStatus,
    ip: str | None = None,
) -> Tenant:
    tenant = await _get_existing_tenant(db, tenant_id)
    tenant.status = status
    await db.flush()
    await record_event(
        db,
        actor=actor,
        action="tenant.status",
        target_type="tenant",
        target_id=str(tenant.id),
        outcome="success",
        ip=ip,
        metadata={"status": status.value},
    )
    return tenant


async def delete_tenant(
    db: AsyncSession,
    *,
    actor: User | None,
    tenant_id: uuid.UUID,
    ip: str | None = None,
) -> None:
    tenant = await _get_existing_tenant(db, tenant_id)
    blockers = await _delete_blockers(db, tenant_id)
    if blockers:
        await record_event(
            db,
            actor=actor,
            action="tenant.delete",
            target_type="tenant",
            target_id=str(tenant.id),
            outcome="denied",
            ip=ip,
            metadata={"blockers": blockers},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Tenant has dependents", "blockers": blockers},
        )

    await record_event(
        db,
        actor=actor,
        action="tenant.delete",
        target_type="tenant",
        target_id=str(tenant.id),
        outcome="success",
        ip=ip,
        metadata={"name": tenant.name},
    )
    await db.delete(tenant)
    await db.flush()


async def _get_existing_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


async def _ensure_unique_name(
    db: AsyncSession,
    name: str,
    *,
    exclude_tenant_id: uuid.UUID | None = None,
) -> None:
    existing = (await db.execute(select(Tenant).where(Tenant.name == name))).scalar_one_or_none()
    if existing is not None and existing.id != exclude_tenant_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tenant already exists")


async def _summary(db: AsyncSession, tenant: Tenant) -> TenantSummary:
    active_allocations = (
        await db.execute(
            select(func.count(AllocatedCIDR.id)).where(
                AllocatedCIDR.tenant_id == tenant.id,
                AllocatedCIDR.status == CIDRStatus.active,
            )
        )
    ).scalar_one()
    users = (
        await db.execute(select(func.count(User.id)).where(User.tenant_id == tenant.id))
    ).scalar_one()
    return TenantSummary(
        tenant=tenant,
        active_allocation_count=active_allocations,
        user_count=users,
    )


async def _delete_blockers(db: AsyncSession, tenant_id: uuid.UUID) -> list[str]:
    user_count = (
        await db.execute(select(func.count(User.id)).where(User.tenant_id == tenant_id))
    ).scalar_one()
    allocation_count = (
        await db.execute(
            select(func.count(AllocatedCIDR.id)).where(
                AllocatedCIDR.tenant_id == tenant_id,
                AllocatedCIDR.status != CIDRStatus.revoked,
            )
        )
    ).scalar_one()

    blockers: list[str] = []
    if user_count:
        blockers.append(f"users:{user_count}")
    if allocation_count:
        blockers.append(f"allocations:{allocation_count}")
    return blockers
