import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import Principal, get_current_user, get_session_store, raise_unauthenticated
from app.core.sessions import RedisSessionStore
from app.db.models import Role, User
from app.db.session import get_db
from app.services import auth as auth_service
from app.services import users as user_service

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class PrincipalResponse(BaseModel):
    id: uuid.UUID
    username: str
    role: Role
    tenant_id: uuid.UUID | None


@router.post("/login", response_model=PrincipalResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    sessions: Annotated[RedisSessionStore, Depends(get_session_store)],
) -> PrincipalResponse:
    result = await auth_service.login(
        db,
        sessions=sessions,
        username=payload.username,
        password=payload.password,
    )
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        result.session_id,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )
    return _principal_response(
        Principal(
            user_id=result.user.id,
            username=result.user.username,
            role=result.user.role,
            tenant_id=result.user.tenant_id,
            session_id=result.session_id,
        )
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    sessions: Annotated[RedisSessionStore, Depends(get_session_store)],
) -> None:
    actor = await db.get(User, principal.user_id)
    await auth_service.logout(db, actor=actor, sessions=sessions, session_id=principal.session_id)
    response.delete_cookie(
        get_settings().session_cookie_name,
        secure=get_settings().cookie_secure,
        samesite=get_settings().cookie_samesite,
    )


@router.get("/me", response_model=PrincipalResponse)
async def me(principal: Annotated[Principal, Depends(get_current_user)]) -> PrincipalResponse:
    return _principal_response(principal)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChangeRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    sessions: Annotated[RedisSessionStore, Depends(get_session_store)],
) -> None:
    user = await db.get(User, principal.user_id)
    if user is None:
        raise_unauthenticated()
    await user_service.change_own_password(
        db,
        user=user,
        sessions=sessions,
        current_session_id=principal.session_id,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )


def _principal_response(principal: Principal) -> PrincipalResponse:
    return PrincipalResponse(
        id=principal.user_id,
        username=principal.username,
        role=principal.role,
        tenant_id=principal.tenant_id,
    )
