from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routers import (
    allocations,
    alerts,
    apply_status,
    auth,
    billing,
    feeds,
    global_blacklist,
    lists,
    rules,
    services,
    telemetry,
    tenants,
    users,
)
from app.core.config import Settings, get_settings
from app.core.redis import close_redis_client
from app.db.session import dispose_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_redis_client()
        await dispose_engine()


_API_PATH_PREFIXES = {
    "allocations",
    "alerts",
    "auth",
    "billing",
    "feeds",
    "health",
    "jobs",
    "lists",
    "node",
    "services",
    "tenants",
    "users",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(allocations.router)
    app.include_router(alerts.router)
    app.include_router(apply_status.router)
    app.include_router(auth.router)
    app.include_router(billing.router)
    app.include_router(feeds.router)
    app.include_router(global_blacklist.router)
    app.include_router(lists.router)
    app.include_router(rules.router)
    app.include_router(services.router)
    app.include_router(telemetry.router)
    app.include_router(tenants.router)
    app.include_router(users.router)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    _configure_frontend_static(app, settings.frontend_static_dir)
    return app


def _configure_frontend_static(app: FastAPI, directory: Path | None) -> None:
    if directory is None:
        return
    index = directory / "index.html"
    if not directory.is_dir() or not index.is_file():
        raise RuntimeError("CONTROL_PLANE_FRONTEND_STATIC_DIR must contain a built Vite index.html")

    assets = directory / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    async def frontend_index() -> FileResponse:
        return FileResponse(index)

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend_history_fallback(path: str) -> FileResponse:
        if (
            path == "assets"
            or path.startswith("assets/")
            or path.split("/", 1)[0] in _API_PATH_PREFIXES
            or Path(path).suffix
        ):
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(index)


app = create_app()
