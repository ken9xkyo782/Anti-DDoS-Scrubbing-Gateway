import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Annotated, Any, NoReturn

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from app.core.config import get_settings
from app.core.sessions import RedisSessionStore
from app.db.models import Role, TenantStatus, User, UserStatus
from app.db.session import get_db


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    username: str
    role: Role
    tenant_id: uuid.UUID | None
    session_id: str


async def get_redis() -> AsyncGenerator[Redis, None]:
    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


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
