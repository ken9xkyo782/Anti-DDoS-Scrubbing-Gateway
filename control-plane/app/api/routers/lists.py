import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.lists import (
    BlacklistEntryResponse,
    ListEntryCreateRequest,
    WhitelistEntryResponse,
)
from app.core.deps import Principal, get_current_user, load_service_for_principal
from app.db.models import BlacklistEntry, BlacklistScope, User, WhitelistEntry
from app.db.session import get_db
from app.services import lists as list_service

router = APIRouter(tags=["lists"])


@router.post(
    "/services/{service_id}/whitelist",
    response_model=WhitelistEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_whitelist(
    service_id: uuid.UUID,
    payload: ListEntryCreateRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WhitelistEntryResponse:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    entry = await list_service.add_whitelist(
        db,
        service_id=service.id,
        source_cidr=payload.source_cidr,
        actor=actor,
    )
    return _whitelist_response(entry)


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


@router.delete("/services/{service_id}/whitelist", status_code=status.HTTP_204_NO_CONTENT)
async def remove_whitelist(
    service_id: uuid.UUID,
    source_cidr: str,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    await list_service.remove_whitelist(
        db,
        service_id=service.id,
        source_cidr=source_cidr,
        actor=actor,
    )


@router.post(
    "/services/{service_id}/blacklist",
    response_model=BlacklistEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_service_blacklist(
    service_id: uuid.UUID,
    payload: ListEntryCreateRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BlacklistEntryResponse:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    entry = await list_service.add_blacklist(
        db,
        scope=BlacklistScope.service,
        service_id=service.id,
        source_cidr=payload.source_cidr,
        actor=actor,
    )
    return _blacklist_response(entry)


@router.get("/services/{service_id}/blacklist", response_model=list[BlacklistEntryResponse])
async def list_service_blacklist(
    service_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[BlacklistEntryResponse]:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    return [
        _blacklist_response(entry)
        for entry in await list_service.list_blacklist(
            db,
            scope=BlacklistScope.service,
            service_id=service.id,
            actor=actor,
        )
    ]


@router.delete("/services/{service_id}/blacklist", status_code=status.HTTP_204_NO_CONTENT)
async def remove_service_blacklist(
    service_id: uuid.UUID,
    source_cidr: str,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await _load_actor(db, principal)
    await list_service.remove_blacklist(
        db,
        scope=BlacklistScope.service,
        service_id=service.id,
        source_cidr=source_cidr,
        actor=actor,
    )


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


def _blacklist_response(entry: BlacklistEntry) -> BlacklistEntryResponse:
    return BlacklistEntryResponse(
        id=entry.id,
        service_id=entry.service_id,
        scope=entry.scope,
        source=entry.source,
        source_cidr=str(entry.source_cidr),
        created_by=entry.created_by,
        created_at=entry.created_at,
    )
