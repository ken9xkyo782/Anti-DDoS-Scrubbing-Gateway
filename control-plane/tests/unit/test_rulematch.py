import pytest

from app.core.rulematch import (
    PortRangeError,
    RuleView,
    find_overlaps,
    rules_overlap,
    validate_port_range,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("lo", "hi"),
    [
        (80, 80),
        (1, 65535),
        (None, None),
    ],
)
def test_validate_port_range_accepts_valid_ranges(lo: int | None, hi: int | None) -> None:
    assert validate_port_range(lo, hi) is None


@pytest.mark.parametrize(
    ("lo", "hi"),
    [
        (80, 79),
        (-1, 10),
        (0, 70000),
        (None, 80),
        (80, None),
    ],
)
def test_validate_port_range_rejects_invalid_ranges(lo: int | None, hi: int | None) -> None:
    with pytest.raises(PortRangeError):
        validate_port_range(lo, hi)


def test_rules_overlap_same_protocol_touching_ranges() -> None:
    assert rules_overlap(
        RuleView(id="a", protocol="tcp", src_port=(100, 200), dst_port=(80, 80)),
        RuleView(id="b", protocol="tcp", src_port=(200, 250), dst_port=(80, 443)),
    )


def test_rules_overlap_same_protocol_nested_ranges() -> None:
    assert rules_overlap(
        RuleView(id="a", protocol="udp", src_port=(1000, 5000), dst_port=(1, 65535)),
        RuleView(id="b", protocol="udp", src_port=(2000, 3000), dst_port=(53, 53)),
    )


def test_rules_overlap_disjoint_ports_are_false() -> None:
    assert not rules_overlap(
        RuleView(id="a", protocol="tcp", src_port=(1, 10), dst_port=(80, 80)),
        RuleView(id="b", protocol="tcp", src_port=(11, 20), dst_port=(80, 80)),
    )


def test_rules_overlap_any_protocol_intersects_specific_protocol() -> None:
    assert rules_overlap(
        RuleView(id="a", protocol="any", src_port=(None, None), dst_port=(None, None)),
        RuleView(id="b", protocol="tcp", src_port=(443, 443), dst_port=(443, 443)),
    )


def test_rules_overlap_icmp_equal_without_ports() -> None:
    assert rules_overlap(
        RuleView(id="a", protocol="icmp", src_port=(None, None), dst_port=(None, None)),
        RuleView(id="b", protocol="icmp", src_port=(None, None), dst_port=(None, None)),
    )


def test_rules_overlap_icmp_vs_udp_is_false() -> None:
    assert not rules_overlap(
        RuleView(id="a", protocol="icmp", src_port=(None, None), dst_port=(None, None)),
        RuleView(id="b", protocol="udp", src_port=(None, None), dst_port=(None, None)),
    )


def test_find_overlaps_returns_every_overlapped_rule() -> None:
    existing = [
        RuleView(id="rule-10", protocol="tcp", src_port=(1, 100), dst_port=(80, 80)),
        RuleView(id="rule-20", protocol="any", src_port=(None, None), dst_port=(None, None)),
        RuleView(id="rule-30", protocol="udp", src_port=(1, 100), dst_port=(53, 53)),
    ]
    candidate = RuleView(id="candidate", protocol="tcp", src_port=(50, 60), dst_port=(80, 80))

    assert [rule.id for rule in find_overlaps(existing, candidate)] == ["rule-10", "rule-20"]


def test_find_overlaps_returns_empty_list_when_disjoint() -> None:
    existing = [
        RuleView(id="rule-10", protocol="tcp", src_port=(1, 100), dst_port=(80, 80)),
        RuleView(id="rule-20", protocol="udp", src_port=(1, 100), dst_port=(53, 53)),
    ]
    candidate = RuleView(id="candidate", protocol="tcp", src_port=(101, 200), dst_port=(443, 443))

    assert find_overlaps(existing, candidate) == []
