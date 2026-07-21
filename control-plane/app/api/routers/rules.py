import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.apply import ApplyMutationResponse
from app.api.schemas.rules import (
    RuleCreateRequest,
    RuleOverlapCheckRequest,
    RuleOverlapCheckResponse,
    RulePatchRequest,
    RuleResponse,
)
from app.core.deps import Principal, get_current_user, load_service_for_principal
from app.db.models import AllowRule, ProtectedService, User
from app.db.session import get_db
from app.services import rules as rule_service

router = APIRouter(prefix="/services/{service_id}/rules", tags=["rules"])


@router.post("", response_model=ApplyMutationResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_rule(
    service_id: uuid.UUID,
    payload: RuleCreateRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApplyMutationResponse:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    result = await rule_service.create_rule(
        db,
        service_id=service.id,
        actor=actor,
        priority=payload.priority,
        protocol=payload.protocol,
        src_port_lo=payload.src_port_lo,
        src_port_hi=payload.src_port_hi,
        dst_port_lo=payload.dst_port_lo,
        dst_port_hi=payload.dst_port_hi,
        enabled=payload.enabled,
    )
    return _apply_mutation_response(result.service)


@router.get("", response_model=list[RuleResponse])
async def list_rules(
    service_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[RuleResponse]:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    return [
        _rule_response(rule)
        for rule in await rule_service.list_rules(db, service_id=service.id, actor=actor)
    ]


@router.patch(
    "/{rule_id}",
    response_model=ApplyMutationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_rule(
    service_id: uuid.UUID,
    rule_id: uuid.UUID,
    payload: RulePatchRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApplyMutationResponse:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    result = await rule_service.update_rule(
        db,
        service_id=service.id,
        rule_id=rule_id,
        actor=actor,
        priority=payload.priority,
        protocol=payload.protocol,
        src_port_lo=payload.src_port_lo,
        src_port_hi=payload.src_port_hi,
        dst_port_lo=payload.dst_port_lo,
        dst_port_hi=payload.dst_port_hi,
        enabled=payload.enabled,
    )
    return _apply_mutation_response(result.service)


@router.delete(
    "/{rule_id}",
    response_model=ApplyMutationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_rule(
    service_id: uuid.UUID,
    rule_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApplyMutationResponse:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    updated = await rule_service.delete_rule(
        db,
        service_id=service.id,
        rule_id=rule_id,
        actor=actor,
    )
    return _apply_mutation_response(updated)


@router.post("/overlap-check", response_model=RuleOverlapCheckResponse)
async def overlap_check(
    service_id: uuid.UUID,
    payload: RuleOverlapCheckRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RuleOverlapCheckResponse:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    warnings = await rule_service.overlap_dry_run(
        db,
        service_id=service.id,
        actor=actor,
        protocol=payload.protocol,
        src_port_lo=payload.src_port_lo,
        src_port_hi=payload.src_port_hi,
        dst_port_lo=payload.dst_port_lo,
        dst_port_hi=payload.dst_port_hi,
    )
    return RuleOverlapCheckResponse(warnings=warnings)


async def _load_actor(db: AsyncSession, principal: Principal) -> User | None:
    return await db.get(User, principal.user_id)


def _rule_response(rule: AllowRule, *, warnings: list[str] | None = None) -> RuleResponse:
    return RuleResponse(
        id=rule.id,
        service_id=rule.service_id,
        priority=rule.priority,
        protocol=rule.protocol,
        src_port_lo=rule.src_port_lo,
        src_port_hi=rule.src_port_hi,
        dst_port_lo=rule.dst_port_lo,
        dst_port_hi=rule.dst_port_hi,
        enabled=rule.enabled,
        warnings=warnings or [],
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


def _apply_mutation_response(service: ProtectedService) -> ApplyMutationResponse:
    return ApplyMutationResponse(
        apply_status=service.apply_status,
        version=service.version,
        active_version=service.active_version,
    )
