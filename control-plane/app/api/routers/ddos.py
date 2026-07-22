from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.ddos import (
    AmplificationConfigResponse,
    BlockedPortCreateRequest,
    BlockedPortResponse,
)
from app.core.deps import Principal, get_current_user, require_admin
from app.db.models import BlockedUdpPort, User
from app.db.session import get_db
from app.services import ddos_amplification as ddos_service

router = APIRouter(prefix="/ddos", tags=["ddos"])


async def get_admin_principal(
    principal: Annotated[Principal, Depends(get_current_user)],
) -> Principal:
    return require_admin(principal)


@router.get("/amplification", response_model=AmplificationConfigResponse)
async def get_amplification_config(
    _principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AmplificationConfigResponse:
    dynamic_entries = await ddos_service.list_blocked_ports(db)
    return AmplificationConfigResponse(
        hardcoded_ports=list(ddos_service.HARDCODED_AMP_PORTS),
        dynamic_ports=[_blocked_port_response(entry) for entry in dynamic_entries],
    )


@router.post(
    "/amplification/ports",
    response_model=BlockedPortResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_blocked_udp_port(
    payload: BlockedPortCreateRequest,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BlockedPortResponse:
    actor = await _load_actor(db, principal)
    entry = await ddos_service.add_blocked_port(
        db,
        actor=actor,
        port=payload.port,
        note=payload.note,
    )
    return _blocked_port_response(entry)


@router.delete("/amplification/ports/{port}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_blocked_udp_port(
    port: int,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    actor = await _load_actor(db, principal)
    await ddos_service.remove_blocked_port(db, actor=actor, port=port)


async def _load_actor(db: AsyncSession, principal: Principal) -> User | None:
    return await db.get(User, principal.user_id)


def _blocked_port_response(entry: BlockedUdpPort) -> BlockedPortResponse:
    return BlockedPortResponse(
        port=entry.port,
        note=entry.note,
        created_by=entry.created_by,
        created_at=entry.created_at,
    )
