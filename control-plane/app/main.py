from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers import (
    allocations,
    apply_status,
    auth,
    global_blacklist,
    lists,
    rules,
    services,
    tenants,
    users,
)
from app.core.config import get_settings
from app.core.redis import close_redis_client
from app.db.session import dispose_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_redis_client()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(allocations.router)
    app.include_router(apply_status.router)
    app.include_router(auth.router)
    app.include_router(global_blacklist.router)
    app.include_router(lists.router)
    app.include_router(rules.router)
    app.include_router(services.router)
    app.include_router(tenants.router)
    app.include_router(users.router)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
