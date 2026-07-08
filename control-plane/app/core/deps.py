import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from ipaddress import IPv4Network
from typing import Annotated, Any, NoReturn

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from app.core.config import get_settings
from app.core.redis import get_redis_client
from app.core.sessions import RedisSessionStore
from app.db.models import ProtectedService, Role, TenantStatus, User, UserStatus
from app.db.session import get_db
from app.services.allocations import cidr_in_tenant_allocation


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    username: str
    role: Role
    tenant_id: uuid.UUID | None
    session_id: str


async def get_redis() -> AsyncGenerator[Redis, None]:
    yield get_redis_client()


def get_session_store(redis: Annotated[Redis, Depends(get_redis)]) -> RedisSessionStore:
    settings = get_settings()
    return RedisSessionStore(
        redis,
        idle_seconds=settings.session_idle_seconds,
        absolute_seconds=settings.session_absolute_seconds,
    )


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    sessions: Annotated[RedisSessionStore, Depends(get_session_store)],
) -> Principal:
    sid = request.cookies.get(get_settings().session_cookie_name)
    if sid is None:
        raise_unauthenticated()
    return await resolve_current_user(sid, db, sessions)


async def resolve_current_user(
    sid: str,
    db: AsyncSession,
    sessions: RedisSessionStore,
) -> Principal:
    session = await sessions.get(sid)
    if session is None:
        raise_unauthenticated()

    user = (
        await db.execute(
            select(User).options(selectinload(User.tenant)).where(User.id == session.user_id)
        )
    ).scalar_one_or_none()
    if user is None or user.status != UserStatus.active:
        raise_unauthenticated()
    if user.session_version != session.session_version:
        raise_unauthenticated()
    if user.role == Role.tenant_user:
        if (
            user.tenant_id is None
            or user.tenant is None
            or user.tenant.status != TenantStatus.active
        ):
            raise_unauthenticated()

    return Principal(
        user_id=user.id,
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
        session_id=sid,
    )


def require_admin(principal: Principal) -> Principal:
    if principal.role != Role.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return principal


def authorize_tenant_resource(
    principal: Principal,
    resource_tenant_id: uuid.UUID | None,
) -> None:
    if principal.role == Role.admin:
        return
    if principal.tenant_id is None or resource_tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if principal.tenant_id != resource_tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


async def require_within_allocation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    target: IPv4Network,
) -> None:
    if not await cidr_in_tenant_allocation(db, tenant_id, target):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


async def load_service_for_principal(
    db: AsyncSession,
    service_id: uuid.UUID,
    principal: Principal,
) -> ProtectedService:
    service = await db.get(ProtectedService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    try:
        authorize_tenant_resource(principal, service.tenant_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Service not found",
            ) from exc
        raise
    return service


def scope_to_tenant(statement: Select[Any], principal: Principal) -> Select[Any]:
    if principal.role == Role.admin:
        return statement
    if principal.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    entity = statement.column_descriptions[0].get("entity")
    tenant_column = getattr(entity, "tenant_id", None)
    if tenant_column is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return statement.where(tenant_column == principal.tenant_id)


def raise_unauthenticated() -> NoReturn:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
