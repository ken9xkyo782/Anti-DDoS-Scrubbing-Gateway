import pytest

from app.services.audit import scrub_metadata

pytestmark = pytest.mark.unit


def test_scrub_metadata_removes_secret_like_keys_recursively() -> None:
    metadata = {
        "username": "alice",
        "password": "plain",
        "api_token": "token",
        "nested": {
            "feed_secret": "secret",
            "credential_id": "credential",
            "safe": "value",
        },
    }

    scrubbed = scrub_metadata(metadata)

    assert scrubbed == {"username": "alice", "nested": {"safe": "value"}}
    assert "password" in metadata


def test_scrub_metadata_handles_none() -> None:
    assert scrub_metadata(None) == {}
