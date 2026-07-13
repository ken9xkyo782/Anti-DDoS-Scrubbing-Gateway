from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app

pytestmark = pytest.mark.unit


def write_bundle(directory: Path) -> None:
    assets = directory / "assets"
    assets.mkdir()
    (directory / "index.html").write_text("<html><body>SPA bundle</body></html>")
    (assets / "app.js").write_text("console.log('bundle')")


async def test_static_bundle_is_opt_in_and_supports_html_only_history_fallback(
    tmp_path: Path,
) -> None:
    write_bundle(tmp_path)
    app = create_app(Settings(frontend_static_dir=str(tmp_path)))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        root = await client.get("/")
        deep_link = await client.get("/tenant")
        index = await client.get("/index.html")
        asset = await client.get("/assets/app.js")
        missing_asset = await client.get("/assets/missing.js")
        missing_api = await client.get("/node/missing")
        missing_file = await client.get("/missing.js")

    assert root.status_code == 200
    assert root.text == "<html><body>SPA bundle</body></html>"
    assert deep_link.status_code == 200
    assert deep_link.text == root.text
    assert index.status_code == 200
    assert asset.status_code == 200
    assert asset.text == "console.log('bundle')"
    assert missing_asset.status_code == 404
    assert missing_api.status_code == 404
    assert missing_file.status_code == 404


async def test_static_serving_is_disabled_without_an_explicit_directory() -> None:
    app = create_app(Settings(frontend_static_dir=None))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/tenant")

    assert response.status_code == 404
