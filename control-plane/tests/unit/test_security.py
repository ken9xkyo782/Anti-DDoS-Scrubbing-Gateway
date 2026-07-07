import re

import pytest

from app.core.security import hash_password, new_session_id, verify_password

pytestmark = pytest.mark.unit


def test_hash_password_returns_argon2id_hash() -> None:
    hashed = hash_password("correct horse battery staple")

    assert hashed.startswith("$argon2id$")
    assert "correct horse battery staple" not in hashed


def test_hash_password_uses_unique_salts() -> None:
    first = hash_password("same password")
    second = hash_password("same password")

    assert first != second


def test_verify_password_accepts_match_and_rejects_mismatch() -> None:
    hashed = hash_password("s3cret-passphrase")

    assert verify_password("s3cret-passphrase", hashed) is True
    assert verify_password("wrong-passphrase", hashed) is False


def test_verify_password_rejects_malformed_hash() -> None:
    assert verify_password("anything", "not-a-real-hash") is False


def test_new_session_id_is_urlsafe_and_unique() -> None:
    first = new_session_id()
    second = new_session_id()

    assert first != second
    assert len(first) >= 43
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)
