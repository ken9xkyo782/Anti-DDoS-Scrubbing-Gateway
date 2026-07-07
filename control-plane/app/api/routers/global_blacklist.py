from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.lists import BlacklistEntryResponse, ListEntryCreateRequest
from app.core.deps import Principal, get_current_user, require_admin
from app.db.models import BlacklistEntry, BlacklistScope, User
from app.db.session import get_db
from app.services import lists as list_service

router = APIRouter(prefix="/blacklist", tags=["blacklist"])


async def get_admin_principal(
    principal: Annotated[Principal, Depends(get_current_user)],
) -> Principal:
    return require_admin(principal)


@router.post("", response_model=BlacklistEntryResponse, status_code=status.HTTP_201_CREATED)
async def add_global_blacklist(
    payload: ListEntryCreateRequest,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BlacklistEntryResponse:
    actor = await _load_actor(db, principal)
    entry = await list_service.add_blacklist(
        db,
        scope=BlacklistScope.global_,
        service_id=None,
        source_cidr=payload.source_cidr,
        actor=actor,
    )
    return _blacklist_response(entry)


@router.get("", response_model=list[BlacklistEntryResponse])
async def list_global_blacklist(
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[BlacklistEntryResponse]:
    actor = await _load_actor(db, principal)
    return [
        _blacklist_response(entry)
        for entry in await list_service.list_blacklist(
            db,
            scope=BlacklistScope.global_,
            service_id=None,
            actor=actor,
        )
    ]


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def remove_global_blacklist(
    source_cidr: str,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    actor = await _load_actor(db, principal)
    await list_service.remove_blacklist(
        db,
        scope=BlacklistScope.global_,
        service_id=None,
        source_cidr=source_cidr,
        actor=actor,
    )


async def _load_actor(db: AsyncSession, principal: Principal) -> User | None:
    return await db.get(User, principal.user_id)


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
