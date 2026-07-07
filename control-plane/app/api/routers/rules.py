import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.rules import (
    RuleCreateRequest,
    RuleOverlapCheckRequest,
    RuleOverlapCheckResponse,
    RulePatchRequest,
    RuleResponse,
)
from app.core.deps import Principal, get_current_user, load_service_for_principal
from app.db.models import AllowRule, User
from app.db.session import get_db
from app.services import rules as rule_service

router = APIRouter(prefix="/services/{service_id}/rules", tags=["rules"])


@router.post("", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    service_id: uuid.UUID,
    payload: RuleCreateRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RuleResponse:
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
        pps=payload.pps,
        bps=payload.bps,
        enabled=payload.enabled,
    )
    return _rule_response(result.rule, warnings=result.warnings)


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


@router.patch("/{rule_id}", response_model=RuleResponse)
async def update_rule(
    service_id: uuid.UUID,
    rule_id: uuid.UUID,
    payload: RulePatchRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RuleResponse:
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
        pps=payload.pps,
        bps=payload.bps,
        enabled=payload.enabled,
    )
    return _rule_response(result.rule, warnings=result.warnings)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    service_id: uuid.UUID,
    rule_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    await rule_service.delete_rule(db, service_id=service.id, rule_id=rule_id, actor=actor)


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
        pps=rule.pps,
        bps=rule.bps,
        enabled=rule.enabled,
        warnings=warnings or [],
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )
