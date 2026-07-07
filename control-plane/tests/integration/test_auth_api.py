from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import router
from app.core.deps import get_session_store
from app.core.security import hash_password, verify_password
from app.core.sessions import RedisSessionStore
from app.db.models import Role, User
from app.db.session import get_db

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def create_admin(
    db_session: AsyncSession,
    *,
    username: str = "api-admin",
    password: str = "admin-pass",
) -> User:
    user = User(username=username, role=Role.admin, password_hash=hash_password(password))
    db_session.add(user)
    await db_session.flush()
    return user


async def make_client(
    db_session: AsyncSession,
    store: RedisSessionStore,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def test_login_me_logout_cookie_flow(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    await create_admin(db_session)
    store = make_store(redis_client)
    async for client in make_client(db_session, store):
        login = await client.post(
            "/auth/login",
            json={"username": "api-admin", "password": "admin-pass"},
        )
        assert login.status_code == 200
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "secure" in login.headers["set-cookie"].lower()
        assert "samesite=lax" in login.headers["set-cookie"].lower()

        me = await client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "api-admin"
        assert me.json()["role"] == "admin"

        logout = await client.post("/auth/logout")
        assert logout.status_code == 204

        after_logout = await client.get("/auth/me")
        assert after_logout.status_code == 401


async def test_wrong_credentials_are_generic_401(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    await create_admin(db_session)
    async for client in make_client(db_session, make_store(redis_client)):
        response = await client.post(
            "/auth/login",
            json={"username": "api-admin", "password": "wrong"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


async def test_unknown_username_is_generic_401(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    async for client in make_client(db_session, make_store(redis_client)):
        response = await client.post(
            "/auth/login",
            json={"username": "missing", "password": "anything"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


async def test_me_without_session_is_401(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    async for client in make_client(db_session, make_store(redis_client)):
        response = await client.get("/auth/me")

    assert response.status_code == 401


async def test_password_change_keeps_current_session_and_revokes_others(
    db_session: AsyncSession,
    redis_client: Redis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = await create_admin(db_session)
    store = make_store(redis_client)
    async for client in make_client(db_session, store):
        login = await client.post(
            "/auth/login",
            json={"username": "api-admin", "password": "admin-pass"},
        )
        assert login.status_code == 200
        other_sid = await store.create(
            user_id=user.id,
            session_version=user.session_version,
            ip=None,
        )

        response = await client.post(
            "/auth/password",
            json={"current_password": "admin-pass", "new_password": "new-api-pass"},
        )

        assert response.status_code == 204
        assert await store.get(other_sid) is None
        assert (await client.get("/auth/me")).status_code == 200
        await db_session.refresh(user)
        assert verify_password("new-api-pass", user.password_hash)
        assert "admin-pass" not in caplog.text
        assert "new-api-pass" not in caplog.text


async def test_password_change_wrong_current_password_is_401_and_logs_no_plaintext(
    db_session: AsyncSession,
    redis_client: Redis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await create_admin(db_session)
    async for client in make_client(db_session, make_store(redis_client)):
        login = await client.post(
            "/auth/login",
            json={"username": "api-admin", "password": "admin-pass"},
        )
        assert login.status_code == 200

        response = await client.post(
            "/auth/password",
            json={"current_password": "wrong-pass", "new_password": "new-api-pass"},
        )

    assert response.status_code == 401
    assert "wrong-pass" not in caplog.text
    assert "new-api-pass" not in caplog.text
