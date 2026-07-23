import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.apply import ApplyMutationResponse
from app.api.schemas.lists import (
    ListEntryCreateRequest,
    WhitelistEntryResponse,
)
from app.core.deps import Principal, get_current_user, load_service_for_principal
from app.db.models import ProtectedService, User, WhitelistEntry
from app.db.session import get_db
from app.services import lists as list_service

router = APIRouter(tags=["lists"])


@router.post(
    "/services/{service_id}/whitelist",
    response_model=ApplyMutationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def add_whitelist(
    service_id: uuid.UUID,
    payload: ListEntryCreateRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApplyMutationResponse:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    await list_service.add_whitelist(
        db,
        service_id=service.id,
        source_cidr=payload.source_cidr,
        actor=actor,
    )
    return _apply_mutation_response(service)


@router.get("/services/{service_id}/whitelist", response_model=list[WhitelistEntryResponse])
async def list_whitelist(
    service_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[WhitelistEntryResponse]:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    return [
        _whitelist_response(entry)
        for entry in await list_service.list_whitelist(db, service_id=service.id, actor=actor)
    ]


@router.delete(
    "/services/{service_id}/whitelist",
    response_model=ApplyMutationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def remove_whitelist(
    service_id: uuid.UUID,
    source_cidr: str,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApplyMutationResponse:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    updated = await list_service.remove_whitelist(
        db,
        service_id=service.id,
        source_cidr=source_cidr,
        actor=actor,
    )
    return _apply_mutation_response(updated)


async def _load_actor(db: AsyncSession, principal: Principal) -> User | None:
    return await db.get(User, principal.user_id)


def _whitelist_response(entry: WhitelistEntry) -> WhitelistEntryResponse:
    return WhitelistEntryResponse(
        id=entry.id,
        service_id=entry.service_id,
        source_cidr=str(entry.source_cidr),
        created_by=entry.created_by,
        created_at=entry.created_at,
    )


def _apply_mutation_response(service: ProtectedService) -> ApplyMutationResponse:
    return ApplyMutationResponse(
        apply_status=service.apply_status,
        version=service.version,
        active_version=service.active_version,
    )
