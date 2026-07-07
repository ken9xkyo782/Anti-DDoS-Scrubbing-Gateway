import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.tenants import TenantCreateRequest, TenantPatchRequest, TenantResponse
from app.core.deps import Principal, get_current_user, require_admin
from app.db.models import TenantStatus, User
from app.db.session import get_db
from app.services import tenants as tenant_service
from app.services.tenants import TenantSummary

router = APIRouter(prefix="/tenants", tags=["tenants"])


async def get_admin_principal(
    principal: Annotated[Principal, Depends(get_current_user)],
) -> Principal:
    return require_admin(principal)


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: TenantCreateRequest,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TenantResponse:
    actor = await _load_actor(db, principal)
    tenant = await tenant_service.create_tenant(db, actor=actor, name=payload.name)
    summary = await tenant_service.get_tenant_summary(db, tenant.id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return _tenant_response(summary)


@router.get("", response_model=list[TenantResponse])
async def list_tenants(
    _principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[TenantResponse]:
    return [_tenant_response(summary) for summary in await tenant_service.list_tenants(db)]


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: uuid.UUID,
    _principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TenantResponse:
    summary = await tenant_service.get_tenant_summary(db, tenant_id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return _tenant_response(summary)


@router.patch("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: uuid.UUID,
    payload: TenantPatchRequest,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TenantResponse:
    actor = await _load_actor(db, principal)
    tenant = await tenant_service.update_tenant(
        db,
        actor=actor,
        tenant_id=tenant_id,
        name=payload.name,
        status=payload.status,
    )
    summary = await tenant_service.get_tenant_summary(db, tenant.id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return _tenant_response(summary)


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
    tenant_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    actor = await _load_actor(db, principal)
    await tenant_service.delete_tenant(db, actor=actor, tenant_id=tenant_id)


@router.post("/{tenant_id}/suspend", response_model=TenantResponse)
async def suspend_tenant(
    tenant_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TenantResponse:
    return await _set_tenant_status(db, principal, tenant_id, TenantStatus.suspended)


@router.post("/{tenant_id}/reactivate", response_model=TenantResponse)
async def reactivate_tenant(
    tenant_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TenantResponse:
    return await _set_tenant_status(db, principal, tenant_id, TenantStatus.active)


async def _set_tenant_status(
    db: AsyncSession,
    principal: Principal,
    tenant_id: uuid.UUID,
    target_status: TenantStatus,
) -> TenantResponse:
    actor = await _load_actor(db, principal)
    tenant = await tenant_service.set_status(
        db,
        actor=actor,
        tenant_id=tenant_id,
        status=target_status,
    )
    summary = await tenant_service.get_tenant_summary(db, tenant.id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return _tenant_response(summary)


async def _load_actor(db: AsyncSession, principal: Principal) -> User | None:
    return await db.get(User, principal.user_id)


def _tenant_response(summary: TenantSummary) -> TenantResponse:
    tenant = summary.tenant
    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        status=tenant.status,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
        active_allocation_count=summary.active_allocation_count,
        user_count=summary.user_count,
    )
