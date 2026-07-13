from pathlib import Path

import pytest

from app.services.feed_reconcile import GlobalDenySnapshot
from app.worker.applier import MAX_GLOBAL_DENY_ENTRIES, serialize_global_snapshot

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_FIXTURE = REPOSITORY_ROOT / "data-plane/tests/fixtures/global_deny_snapshot_golden.bin"

pytestmark = pytest.mark.unit


def test_serialize_global_snapshot_matches_global_deny_golden_fixture() -> None:
    snapshot = GlobalDenySnapshot(
        revision=42,
        digest="unused-for-wire-format",
        cidrs=("203.0.113.5/32", "192.0.2.0/24", "45.45.0.0/16"),
    )

    assert serialize_global_snapshot(snapshot) == GOLDEN_FIXTURE.read_bytes()


def test_serialize_global_snapshot_rejects_entry_limit() -> None:
    snapshot = GlobalDenySnapshot(
        revision=1,
        digest="unused-for-wire-format",
        cidrs=("192.0.2.0/24",) * (MAX_GLOBAL_DENY_ENTRIES + 1),
    )

    with pytest.raises(ValueError, match="entry limit"):
        serialize_global_snapshot(snapshot)
