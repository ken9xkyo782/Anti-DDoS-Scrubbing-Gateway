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


async def test_static_serving_intercepts_html_accept_headers(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    app = create_app(Settings(frontend_static_dir=str(tmp_path)))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        # GET /services with Accept: text/html -> should return SPA index
        resp_html = await client.get("/services", headers={"accept": "text/html"})
        assert resp_html.status_code == 200
        assert resp_html.text == "<html><body>SPA bundle</body></html>"

        # GET /services/ea5d6736-df96-4b3a-9197-eebbe0d4f59a with Accept: text/html
        # -> should return SPA index
        resp_html_detail = await client.get(
            "/services/ea5d6736-df96-4b3a-9197-eebbe0d4f59a",
            headers={"accept": "text/html"},
        )
        assert resp_html_detail.status_code == 200
        assert resp_html_detail.text == "<html><body>SPA bundle</body></html>"

        # GET /services without Accept: text/html
        # -> should not return SPA (should hit API/auth, return 401)
        resp_api = await client.get("/services")
        assert resp_api.status_code == 401
        assert "SPA bundle" not in resp_api.text

        # GET /docs with Accept: text/html -> should bypass and return Swagger HTML
        resp_docs = await client.get("/docs", headers={"accept": "text/html"})
        assert resp_docs.status_code == 200
        assert "SPA bundle" not in resp_docs.text
