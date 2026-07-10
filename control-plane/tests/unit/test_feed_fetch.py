import asyncio
import logging
from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.db.models import ThreatFeedSource
from app.services.feed_fetch import FeedFetchError, fetch_line_list

pytestmark = pytest.mark.unit


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.iterated = False
        self.yielded = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.iterated = True
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        pass


class SlowStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        await asyncio.Event().wait()
        yield b"unreachable"

    async def aclose(self) -> None:
        pass


def source(
    *,
    url: str = "https://feeds.example.test/deny.txt",
    credential_env_var: str | None = None,
) -> ThreatFeedSource:
    return ThreatFeedSource(
        name="Example feed",
        url=url,
        sync_interval_seconds=300,
        credential_env_var=credential_env_var,
    )


def settings(**values: float | int) -> Settings:
    return Settings(**values)


def test_fetch_settings_defaults_are_bounded() -> None:
    configured = settings()

    assert configured.feed_fetch_connect_timeout_seconds == 5.0
    assert configured.feed_fetch_read_timeout_seconds == 10.0
    assert configured.feed_fetch_write_timeout_seconds == 5.0
    assert configured.feed_fetch_pool_timeout_seconds == 5.0
    assert configured.feed_fetch_wall_timeout_seconds == 30.0
    assert configured.feed_fetch_max_decoded_body_bytes == 32 * 1024 * 1024


@pytest.mark.parametrize(
    "field",
    [
        "feed_fetch_connect_timeout_seconds",
        "feed_fetch_read_timeout_seconds",
        "feed_fetch_write_timeout_seconds",
        "feed_fetch_pool_timeout_seconds",
        "feed_fetch_wall_timeout_seconds",
        "feed_fetch_max_decoded_body_bytes",
    ],
)
def test_fetch_settings_reject_non_positive_limits(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: 0})


async def test_fetch_line_list_returns_streamed_decoded_bytes() -> None:
    stream = ChunkedStream([b"198.51.100.0/24\n", b"203.0.113.0/24\n"])

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_line_list(source(), client, settings())

    assert result.body == b"198.51.100.0/24\n203.0.113.0/24\n"
    assert stream.iterated is True


async def test_fetch_line_list_sends_runtime_bearer_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = "credential-value-that-must-not-leak"
    monkeypatch.setenv("THREAT_FEED_TOKEN", credential)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {credential}"
        return httpx.Response(200, content=b"198.51.100.0/24\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_line_list(
            source(credential_env_var="THREAT_FEED_TOKEN"), client, settings()
        )

    assert result.body == b"198.51.100.0/24\n"


async def test_fetch_line_list_rejects_missing_credential_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISSING_THREAT_FEED_TOKEN", raising=False)
    requested = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FeedFetchError, match="credential"):
            await fetch_line_list(
                source(credential_env_var="MISSING_THREAT_FEED_TOKEN"), client, settings()
            )

    assert requested is False


async def test_fetch_line_list_rejects_non_https_before_request() -> None:
    requested = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FeedFetchError, match="HTTPS"):
            await fetch_line_list(
                source(url="http://feeds.example.test/deny.txt"), client, settings()
            )

    assert requested is False


@pytest.mark.parametrize("status_code", [302, 404, 500])
async def test_fetch_line_list_rejects_redirects_and_non_success_statuses(status_code: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, headers={"Location": "https://other.example.test/feed"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FeedFetchError, match="successful"):
            await fetch_line_list(source(), client, settings())


async def test_fetch_line_list_rejects_oversized_content_length_before_reading() -> None:
    stream = ChunkedStream([b"too much data"])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Length": "6"}, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FeedFetchError, match="size"):
            await fetch_line_list(source(), client, settings(feed_fetch_max_decoded_body_bytes=5))

    assert stream.iterated is False


async def test_fetch_line_list_stops_when_decoded_stream_exceeds_cap() -> None:
    stream = ChunkedStream([b"abc", b"def", b"must not be read"])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FeedFetchError, match="size"):
            await fetch_line_list(source(), client, settings(feed_fetch_max_decoded_body_bytes=5))

    assert stream.yielded == 2


async def test_fetch_line_list_fails_cleanly_for_request_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network failure", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FeedFetchError, match="request"):
            await fetch_line_list(source(), client, settings())


async def test_fetch_line_list_fails_cleanly_when_wall_clock_expires() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=SlowStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FeedFetchError, match="timed out"):
            await fetch_line_list(source(), client, settings(feed_fetch_wall_timeout_seconds=0.01))


async def test_fetch_line_list_never_exposes_credential_or_body_in_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    credential_name = "PRIVATE_FEED_CREDENTIAL_NAME"
    credential_value = "private-feed-credential-value"
    body = "private response body"
    monkeypatch.setenv(credential_name, credential_value)
    caplog.set_level(logging.DEBUG)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=body.encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FeedFetchError) as raised:
            await fetch_line_list(source(credential_env_var=credential_name), client, settings())

    rendered_error = str(raised.value)
    for secret in (credential_name, credential_value, body):
        assert secret not in rendered_error
        assert secret not in caplog.text
