import uuid
from ipaddress import IPv4Network
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.allocations import (
    AllocationCreateRequest,
    AllocationResponse,
    AllocationUsageResponse,
    OverlapCheckRequest,
    OverlapCheckResponse,
)
from app.core.cidr import CidrValidationError, parse_ipv4_cidr, reject_reserved
from app.core.deps import Principal, get_current_user, require_admin
from app.db.models import AllocatedCIDR, CIDRStatus, User
from app.db.session import get_db
from app.services import allocations as allocation_service

router = APIRouter(tags=["allocations"])


async def get_admin_principal(
    principal: Annotated[Principal, Depends(get_current_user)],
) -> Principal:
    return require_admin(principal)


@router.post(
    "/allocations",
    response_model=AllocationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_allocation(
    payload: AllocationCreateRequest,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AllocationResponse:
    actor = await _load_actor(db, principal)
    allocation = await allocation_service.allocate(
        db,
        tenant_id=payload.tenant_id,
        cidr=_validate_cidr(payload.cidr),
        actor=actor,
    )
    return _allocation_response(allocation)


@router.get("/allocations", response_model=list[AllocationUsageResponse])
async def list_allocations(
    tenant_id: uuid.UUID,
    _principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[AllocationUsageResponse]:
    return [_usage_response(row) for row in await allocation_service.list_for_tenant(db, tenant_id)]


@router.post("/allocations/overlap-check", response_model=OverlapCheckResponse)
async def overlap_check(
    payload: OverlapCheckRequest,
    _principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OverlapCheckResponse:
    result = await allocation_service.overlap_check(db, _validate_cidr(payload.cidr))
    return OverlapCheckResponse(
        overlaps=result.overlaps,
        conflicts=[_allocation_response(conflict) for conflict in result.conflicts],
    )


@router.post("/allocations/{allocation_id}/revoke", response_model=AllocationResponse)
async def revoke_allocation(
    allocation_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AllocationResponse:
    actor = await _load_actor(db, principal)
    allocation = await allocation_service.revoke(db, allocation_id=allocation_id, actor=actor)
    return _allocation_response(allocation)


@router.get("/me/allocations", response_model=list[AllocationResponse])
async def list_my_allocations(
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[AllocationResponse]:
    tenant_id = _require_tenant_scope(principal)
    rows = await allocation_service.list_for_tenant(db, tenant_id)
    return [
        _allocation_response(row.allocation)
        for row in rows
        if row.allocation.status == CIDRStatus.active
    ]


@router.get("/me/allocations/{allocation_id}", response_model=AllocationResponse)
async def get_my_allocation(
    allocation_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AllocationResponse:
    tenant_id = _require_tenant_scope(principal)
    allocation = await db.get(AllocatedCIDR, allocation_id)
    if (
        allocation is None
        or allocation.tenant_id != tenant_id
        or allocation.status != CIDRStatus.active
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Allocation not found")
    return _allocation_response(allocation)


async def _load_actor(db: AsyncSession, principal: Principal) -> User | None:
    return await db.get(User, principal.user_id)


def _validate_cidr(value: str) -> IPv4Network:
    try:
        cidr = parse_ipv4_cidr(value)
        reject_reserved(cidr)
    except CidrValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return cidr


def _require_tenant_scope(principal: Principal) -> uuid.UUID:
    if principal.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return principal.tenant_id


def _usage_response(row: allocation_service.AllocationUsage) -> AllocationUsageResponse:
    return AllocationUsageResponse(
        allocation=_allocation_response(row.allocation),
        dependent_count=row.dependent_count,
    )


def _allocation_response(allocation: AllocatedCIDR) -> AllocationResponse:
    return AllocationResponse(
        id=allocation.id,
        tenant_id=allocation.tenant_id,
        cidr=str(allocation.cidr),
        status=allocation.status,
        allocated_by=allocation.allocated_by,
        created_at=allocation.created_at,
        updated_at=allocation.updated_at,
    )
