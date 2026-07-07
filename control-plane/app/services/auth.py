from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.core.sessions import RedisSessionStore
from app.db.models import Role, TenantStatus, User, UserStatus
from app.services.audit import record_event


@dataclass(frozen=True)
class LoginResult:
    session_id: str
    user: User


async def login(
    db: AsyncSession,
    *,
    sessions: RedisSessionStore,
    username: str,
    password: str,
    ip: str | None = None,
) -> LoginResult:
    user = (
        await db.execute(
            select(User).options(selectinload(User.tenant)).where(User.username == username)
        )
    ).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        await _failed_login(db, user, username=username, ip=ip)

    if user.status != UserStatus.active:
        await _failed_login(db, user, username=username, ip=ip)
    if user.role == Role.tenant_user:
        if user.tenant is None or user.tenant.status != TenantStatus.active:
            await _failed_login(db, user, username=username, ip=ip)

    user.last_login_at = datetime.now(UTC)
    session_id = await sessions.create(
        user_id=user.id,
        session_version=user.session_version,
        ip=ip,
    )
    await db.flush()
    await record_event(
        db,
        actor=user,
        action="auth.login",
        target_type="user",
        target_id=str(user.id),
        outcome="success",
        ip=ip,
    )
    return LoginResult(session_id=session_id, user=user)


async def logout(
    db: AsyncSession,
    *,
    actor: User | None,
    sessions: RedisSessionStore,
    session_id: str,
    ip: str | None = None,
) -> None:
    await sessions.revoke(session_id)
    await record_event(
        db,
        actor=actor,
        action="auth.logout",
        target_type="session",
        target_id=session_id,
        outcome="success",
        ip=ip,
    )


async def bootstrap_admin(
    db: AsyncSession,
    *,
    username: str | None = None,
    password: str | None = None,
    ip: str | None = None,
) -> User:
    existing = (
        (await db.execute(select(User).where(User.role == Role.admin).order_by(User.created_at)))
        .scalars()
        .first()
    )
    if existing is not None:
        return existing

    settings = get_settings()
    bootstrap_username = username or settings.bootstrap_admin_username
    bootstrap_password = password or settings.bootstrap_admin_password
    if not bootstrap_username or not bootstrap_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Bootstrap credentials are required",
        )

    user = User(
        username=bootstrap_username,
        password_hash=hash_password(bootstrap_password),
        role=Role.admin,
        status=UserStatus.active,
    )
    db.add(user)
    await db.flush()
    await record_event(
        db,
        actor=None,
        action="auth.bootstrap",
        target_type="user",
        target_id=str(user.id),
        outcome="success",
        ip=ip,
        metadata={"username": bootstrap_username},
    )
    return user


async def _failed_login(
    db: AsyncSession,
    actor: User | None,
    *,
    username: str,
    ip: str | None,
) -> NoReturn:
    await record_event(
        db,
        actor=actor,
        action="auth.login.failed",
        target_type="user",
        target_id=str(actor.id) if actor is not None else None,
        outcome="denied",
        ip=ip,
        metadata={"username": username},
    )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
