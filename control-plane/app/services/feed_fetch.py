import asyncio
import os
from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.db.models import ThreatFeedSource


class FeedFetchError(Exception):
    """A credential-safe failure while retrieving a threat feed."""


@dataclass(frozen=True, slots=True)
class FetchResult:
    body: bytes


def create_feed_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        verify=True,
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(
            connect=settings.feed_fetch_connect_timeout_seconds,
            read=settings.feed_fetch_read_timeout_seconds,
            write=settings.feed_fetch_write_timeout_seconds,
            pool=settings.feed_fetch_pool_timeout_seconds,
        ),
    )


async def fetch_line_list(
    source: ThreatFeedSource,
    client: httpx.AsyncClient,
    settings: Settings,
) -> FetchResult:
    if not _is_https_url(source.url):
        raise FeedFetchError("Feed source must use HTTPS")

    headers = _credential_headers(source.credential_env_var)

    try:
        async with asyncio.timeout(settings.feed_fetch_wall_timeout_seconds):
            async with client.stream(
                "GET", source.url, headers=headers, follow_redirects=False
            ) as response:
                if not response.is_success:
                    raise FeedFetchError("Feed response was not successful")

                content_length = _content_length(response)
                if (
                    content_length is not None
                    and content_length > settings.feed_fetch_max_decoded_body_bytes
                ):
                    raise FeedFetchError("Feed response exceeds the configured size limit")

                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > settings.feed_fetch_max_decoded_body_bytes:
                        raise FeedFetchError("Feed response exceeds the configured size limit")
                    body.extend(chunk)
    except TimeoutError:
        raise FeedFetchError("Feed request timed out") from None
    except httpx.TimeoutException:
        raise FeedFetchError("Feed request timed out") from None
    except (httpx.HTTPError, ValueError):
        raise FeedFetchError("Feed request failed") from None

    return FetchResult(body=bytes(body))


def _is_https_url(url: str) -> bool:
    try:
        return httpx.URL(url).scheme == "https"
    except httpx.InvalidURL:
        return False


def _credential_headers(credential_env_var: str | None) -> dict[str, str]:
    if credential_env_var is None:
        return {}

    try:
        credential = os.environ[credential_env_var]
    except KeyError:
        raise FeedFetchError("Feed credential is unavailable") from None

    return {"Authorization": f"Bearer {credential}"}


def _content_length(response: httpx.Response) -> int | None:
    value = response.headers.get("Content-Length")
    if value is None:
        return None

    try:
        return int(value)
    except ValueError:
        return None
