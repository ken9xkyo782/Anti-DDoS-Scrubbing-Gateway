from ipaddress import IPv4Network

import pytest

from app.core.cidr import CidrValidationError, is_subnet, parse_ipv4_cidr, reject_reserved

pytestmark = pytest.mark.unit


def test_parse_ipv4_cidr_accepts_canonical_network_and_host() -> None:
    assert parse_ipv4_cidr("203.0.113.0/24") == IPv4Network("203.0.113.0/24")
    assert parse_ipv4_cidr("203.0.113.10/32") == IPv4Network("203.0.113.10/32")


@pytest.mark.parametrize("value", ["2001:db8::/32", "not-a-cidr"])
def test_parse_ipv4_cidr_rejects_ipv6_and_malformed_values(value: str) -> None:
    with pytest.raises(CidrValidationError):
        parse_ipv4_cidr(value)


def test_parse_ipv4_cidr_rejects_host_bits_with_canonical_hint() -> None:
    with pytest.raises(CidrValidationError) as exc_info:
        parse_ipv4_cidr("10.0.0.5/24")

    assert "10.0.0.0/24" in str(exc_info.value)


@pytest.mark.parametrize("value", ["0.0.0.0/0", "0.0.0.0/8"])
def test_reject_reserved_denies_whole_or_unspecified_space(value: str) -> None:
    with pytest.raises(CidrValidationError):
        reject_reserved(IPv4Network(value))


@pytest.mark.parametrize("value", ["10.0.0.0/24", "203.0.113.0/24"])
def test_reject_reserved_permits_normal_ranges(value: str) -> None:
    reject_reserved(IPv4Network(value))


def test_is_subnet_matches_sql_containment_semantics() -> None:
    container = IPv4Network("203.0.113.0/24")

    assert is_subnet(IPv4Network("203.0.113.10/32"), container)
    assert not is_subnet(IPv4Network("203.0.112.0/23"), container)
