import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Principal, get_current_user, get_session_store, require_admin
from app.core.sessions import RedisSessionStore
from app.db.models import Role, User, UserStatus
from app.db.session import get_db
from app.services import users as user_service

router = APIRouter(prefix="/users", tags=["users"])


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: Role
    tenant_id: uuid.UUID | None = None


class UserPatchRequest(BaseModel):
    username: str | None = None
    role: Role | None = None
    tenant_id: uuid.UUID | None = None
    status: UserStatus | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    role: Role
    tenant_id: uuid.UUID | None
    tenant_name: str | None
    status: UserStatus
    last_login_at: datetime | None


async def get_admin_principal(
    principal: Annotated[Principal, Depends(get_current_user)],
) -> Principal:
    return require_admin(principal)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateRequest,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
    sessions: Annotated[RedisSessionStore, Depends(get_session_store)],
) -> UserResponse:
    actor = await _load_actor(db, principal)
    user = await user_service.create_user(
        db,
        actor=actor,
        sessions=sessions,
        username=payload.username,
        password=payload.password,
        role=payload.role,
        tenant_id=payload.tenant_id,
    )
    return _user_response(user)


@router.get("", response_model=list[UserResponse])
async def list_users(
    _principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[UserResponse]:
    return [_user_response(user) for user in await user_service.list_users(db)]


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: uuid.UUID,
    _principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    user = await user_service.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _user_response(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    payload: UserPatchRequest,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
    sessions: Annotated[RedisSessionStore, Depends(get_session_store)],
) -> UserResponse:
    actor = await _load_actor(db, principal)
    user = await user_service.update_user(
        db,
        actor=actor,
        sessions=sessions,
        user_id=user_id,
        username=payload.username,
        role=payload.role,
        tenant_id=payload.tenant_id,
    )
    if payload.status is not None:
        user = await user_service.set_status(
            db,
            actor=actor,
            sessions=sessions,
            user_id=user_id,
            status=payload.status,
        )
    return _user_response(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
    sessions: Annotated[RedisSessionStore, Depends(get_session_store)],
) -> None:
    actor = await _load_actor(db, principal)
    await user_service.delete_user(db, actor=actor, sessions=sessions, user_id=user_id)


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: uuid.UUID,
    payload: ResetPasswordRequest,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
    sessions: Annotated[RedisSessionStore, Depends(get_session_store)],
) -> None:
    actor = await _load_actor(db, principal)
    await user_service.reset_password(
        db,
        actor=actor,
        sessions=sessions,
        user_id=user_id,
        new_password=payload.new_password,
    )


async def _load_actor(db: AsyncSession, principal: Principal) -> User | None:
    return await db.get(User, principal.user_id)


def _user_response(user: User) -> UserResponse:
    tenant = user.__dict__.get("tenant")
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
        tenant_name=tenant.name if tenant is not None else None,
        status=user.status,
        last_login_at=user.last_login_at,
    )
