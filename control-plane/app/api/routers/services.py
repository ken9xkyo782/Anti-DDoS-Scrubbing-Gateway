import uuid
from ipaddress import IPv4Network
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.services import (
    ServiceCreateRequest,
    ServiceDisableRequest,
    ServicePatchRequest,
    ServicePlanPatchRequest,
    ServicePlanResponse,
    ServiceResponse,
)
from app.core.cidr import CidrValidationError, parse_ipv4_cidr, reject_reserved
from app.core.deps import (
    Principal,
    get_current_user,
    load_service_for_principal,
    require_admin,
)
from app.db.models import Role, User
from app.db.session import get_db
from app.services import services as service_service

router = APIRouter(prefix="/services", tags=["services"])


async def get_admin_principal(
    principal: Annotated[Principal, Depends(get_current_user)],
) -> Principal:
    return require_admin(principal)


@router.post("", response_model=ServiceResponse, status_code=status.HTTP_201_CREATED)
async def create_service(
    payload: ServiceCreateRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ServiceResponse:
    actor = await _load_actor(db, principal)
    tenant_id = _tenant_for_create(payload.tenant_id, principal)
    record = await service_service.create_service(
        db,
        tenant_id=tenant_id,
        name=payload.name,
        cidr_or_ip=_validate_cidr(payload.cidr_or_ip),
        actor=actor,
        mode=payload.mode,
        vip_pps=payload.vip_pps,
        vip_bps=payload.vip_bps,
        committed_clean_gbps=(
            payload.plan.committed_clean_gbps if payload.plan is not None else None
        ),
        ceiling_clean_gbps=payload.plan.ceiling_clean_gbps if payload.plan is not None else None,
    )
    return _service_response(record)


@router.get("", response_model=list[ServiceResponse])
async def list_services(
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ServiceResponse]:
    return [
        _service_response(record) for record in await service_service.list_services(db, principal)
    ]


@router.get("/{service_id}", response_model=ServiceResponse)
async def get_service(
    service_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ServiceResponse:
    service = await load_service_for_principal(db, service_id, principal)
    return _service_response(
        await service_service.get_service(db, service_id=service.id, principal=principal)
    )


@router.patch("/{service_id}", response_model=ServiceResponse)
async def update_service(
    service_id: uuid.UUID,
    payload: ServicePatchRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ServiceResponse:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    record = await service_service.update_service(
        db,
        service_id=service.id,
        actor=actor,
        name=payload.name,
        cidr_or_ip=_validate_cidr(payload.cidr_or_ip) if payload.cidr_or_ip is not None else None,
        mode=payload.mode,
        vip_pps=payload.vip_pps,
        vip_bps=payload.vip_bps,
    )
    return _service_response(record)


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(
    service_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    await service_service.delete_service(db, service_id=service.id, actor=actor)


@router.post("/{service_id}/enable", response_model=ServiceResponse)
async def enable_service(
    service_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ServiceResponse:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    await service_service.set_enabled(db, service_id=service.id, enabled=True, actor=actor)
    return _service_response(
        await service_service.get_service(db, service_id=service.id, principal=principal)
    )


@router.post("/{service_id}/disable", response_model=ServiceResponse)
async def disable_service(
    service_id: uuid.UUID,
    payload: ServiceDisableRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ServiceResponse:
    if not payload.confirm:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Confirmation required",
        )
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    await service_service.set_enabled(db, service_id=service.id, enabled=False, actor=actor)
    return _service_response(
        await service_service.get_service(db, service_id=service.id, principal=principal)
    )


@router.patch("/{service_id}/plan", response_model=ServiceResponse)
async def update_service_plan(
    service_id: uuid.UUID,
    payload: ServicePlanPatchRequest,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ServiceResponse:
    actor = await _load_actor(db, principal)
    result = await service_service.size_plan(
        db,
        service_id=service_id,
        actor=actor,
        committed_clean_gbps=payload.committed_clean_gbps,
        ceiling_clean_gbps=payload.ceiling_clean_gbps,
    )
    record = await service_service.get_service(
        db, service_id=result.service.id, principal=principal
    )
    return _service_response(record, warnings=result.warnings)


async def _load_actor(db: AsyncSession, principal: Principal) -> User | None:
    return await db.get(User, principal.user_id)


def _tenant_for_create(tenant_id: uuid.UUID | None, principal: Principal) -> uuid.UUID:
    if principal.role == Role.admin:
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="tenant_id is required",
            )
        return tenant_id
    if principal.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if tenant_id is not None and tenant_id != principal.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return principal.tenant_id


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


def _service_response(
    record: service_service.ServiceRecord,
    *,
    warnings: list[str] | None = None,
) -> ServiceResponse:
    service = record.service
    return ServiceResponse(
        id=service.id,
        tenant_id=service.tenant_id,
        tenant_name=record.tenant.name,
        created_by=service.created_by,
        creator_username=record.creator.username if record.creator is not None else None,
        name=service.name,
        cidr_or_ip=str(service.cidr_or_ip),
        mode=service.mode,
        enabled=service.enabled,
        vip_pps=service.vip_pps,
        vip_bps=service.vip_bps,
        apply_status=service.apply_status,
        version=service.version,
        active_version=service.active_version,
        plan=ServicePlanResponse(
            committed_clean_gbps=record.plan.committed_clean_gbps,
            ceiling_clean_gbps=record.plan.ceiling_clean_gbps,
            billing_metric=record.plan.billing_metric,
            overage_policy=record.plan.overage_policy.value,
        ),
        warnings=warnings or [],
        created_at=service.created_at,
        updated_at=service.updated_at,
    )
