from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.models import AllowRule, BlacklistEntry, Protocol, ServicePlan, WhitelistEntry
from app.worker.applier import ServiceConfig, serialize_node_snapshot

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_FIXTURE = REPOSITORY_ROOT / "data-plane/tests/fixtures/apply_snapshot_golden.bin"


@pytest.mark.unit
def test_serialize_node_snapshot_matches_apply_snapshot_golden_fixture() -> None:
    rich = ServiceConfig(
        service_id=uuid4(),
        dp_id=42,
        version=1,
        name="golden-rich",
        cidr_or_ip="10.0.0.2/32",
        mode="allow-rule-only",
        enabled=True,
        vip_pps=1_000,
        vip_bps=8_000_000,
        service_pps=None,
        service_bps=None,
        plan=ServicePlan(
            committed_clean_gbps=Decimal("1"),
            ceiling_clean_gbps=Decimal("2"),
        ),
        rules=(
            AllowRule(
                priority=1,
                protocol=Protocol.any,
                src_port_lo=0,
                src_port_hi=65_535,
                dst_port_lo=0,
                dst_port_hi=65_535,
                enabled=True,
            ),
        ),
        whitelist=(WhitelistEntry(source_cidr="192.51.100.0/24"),),
        blacklist=(BlacklistEntry(source_cidr="203.0.113.5/32"),),
    )
    minimal = ServiceConfig(
        service_id=uuid4(),
        dp_id=43,
        version=1,
        name="golden-minimal",
        cidr_or_ip="10.0.0.3/32",
        mode="allow-rule-only",
        enabled=True,
        vip_pps=None,
        vip_bps=None,
        service_pps=None,
        service_bps=None,
        plan=ServicePlan(
            committed_clean_gbps=Decimal("0"),
            ceiling_clean_gbps=Decimal("0.5"),
        ),
        rules=(),
        whitelist=(),
        blacklist=(),
    )

    snapshot = serialize_node_snapshot((rich, minimal))

    assert snapshot == GOLDEN_FIXTURE.read_bytes()
    assert snapshot[8:20] == b"\x03\x00\x00\x00\x01\x00\x00\x00\x02\x00\x00\x00"
