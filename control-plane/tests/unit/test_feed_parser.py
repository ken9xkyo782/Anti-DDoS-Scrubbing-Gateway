import pytest

from app.core.feed_parser import (
    MAX_INVALID_LINE_DIAGNOSTICS,
    ParseOutcome,
    parse_line_list,
)

pytestmark = pytest.mark.unit


def test_parse_line_list_normalizes_utf8_bom_comments_and_whitespace() -> None:
    result = parse_line_list(
        b"\xef\xbb\xbf# feed heading\n"
        b"  198.51.100.3  # individual host\n"
        b"\t203.0.113.0/24; network\n"
        b"; comment only\n"
        b"\n"
    )

    assert result.outcome is ParseOutcome.success
    assert result.physical_line_count == 5
    assert result.cidrs == ("198.51.100.3/32", "203.0.113.0/24")
    assert result.valid_distinct_count == 2
    assert result.invalid_count == 0
    assert result.duplicate_count == 0
    assert result.invalid_line_diagnostics == ()


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("198.51.100.3", "198.51.100.3/32"),
        ("198.51.100.0/24", "198.51.100.0/24"),
        ("\t198.51.100.3\t", "198.51.100.3/32"),
    ],
)
def test_parse_line_list_canonicalizes_valid_entries(line: str, expected: str) -> None:
    result = parse_line_list(line.encode())

    assert result.outcome is ParseOutcome.success
    assert result.cidrs == (expected,)
    assert result.valid_distinct_count == 1


def test_parse_line_list_strips_the_first_inline_comment_delimiter() -> None:
    result = parse_line_list(b"198.51.100.0/24; semicolon # hash\n203.0.113.0/24#hash;semicolon")

    assert result.outcome is ParseOutcome.success
    assert result.cidrs == ("198.51.100.0/24", "203.0.113.0/24")


def test_parse_line_list_collapses_only_exact_canonical_duplicates() -> None:
    result = parse_line_list(b"198.51.100.3\n198.51.100.3/32\n198.51.100.3 # repeat")

    assert result.outcome is ParseOutcome.success
    assert result.cidrs == ("198.51.100.3/32",)
    assert result.valid_distinct_count == 1
    assert result.duplicate_count == 2


def test_parse_line_list_preserves_containing_and_contained_ranges() -> None:
    result = parse_line_list(b"10.0.0.0/8\n10.0.0.0/24\n10.0.0.1")

    assert result.outcome is ParseOutcome.success
    assert result.cidrs == ("10.0.0.0/8", "10.0.0.0/24", "10.0.0.1/32")
    assert result.duplicate_count == 0


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("not-a-cidr", "Invalid CIDR"),
        ("2001:db8::/32", "IPv6 CIDRs are not supported"),
        ("10.0.0.1/24", "CIDR has host bits set; canonical network is 10.0.0.0/24"),
        ("0.0.0.0/0", "CIDR /0 is not allowed"),
        ("192.0.2.0/33", "Invalid CIDR"),
        ("192.0.2.0/24/", "Invalid CIDR"),
    ],
)
def test_parse_line_list_reports_each_invalid_entry_with_reason(line: str, reason: str) -> None:
    result = parse_line_list(f"198.51.100.0/24\n{line}".encode())

    assert result.outcome is ParseOutcome.partial
    assert result.cidrs == ("198.51.100.0/24",)
    assert result.invalid_count == 1
    assert result.invalid_line_diagnostics[0].line_number == 2
    assert result.invalid_line_diagnostics[0].reason == reason


def test_parse_line_list_all_invalid_entries_have_failed_outcome() -> None:
    result = parse_line_list(b"not-a-cidr\n10.0.0.1/24")

    assert result.outcome is ParseOutcome.failed
    assert result.physical_line_count == 2
    assert result.cidrs == ()
    assert result.valid_distinct_count == 0
    assert result.invalid_count == 2
    assert result.duplicate_count == 0
    assert [diagnostic.line_number for diagnostic in result.invalid_line_diagnostics] == [1, 2]


def test_parse_line_list_records_partial_counts_for_mixed_input() -> None:
    result = parse_line_list(b"198.51.100.0/24\ninvalid\n198.51.100.0/24")

    assert result.outcome is ParseOutcome.partial
    assert result.physical_line_count == 3
    assert result.valid_distinct_count == 1
    assert result.invalid_count == 1
    assert result.duplicate_count == 1


def test_parse_line_list_bounds_invalid_line_diagnostics() -> None:
    result = parse_line_list(
        b"\n".join(b"invalid" for _ in range(MAX_INVALID_LINE_DIAGNOSTICS + 1))
    )

    assert result.outcome is ParseOutcome.failed
    assert result.invalid_count == MAX_INVALID_LINE_DIAGNOSTICS + 1
    assert len(result.invalid_line_diagnostics) == MAX_INVALID_LINE_DIAGNOSTICS
    assert result.invalid_line_diagnostics[-1].line_number == MAX_INVALID_LINE_DIAGNOSTICS


def test_parse_line_list_invalid_utf8_is_an_explicit_parser_failure() -> None:
    result = parse_line_list(b"198.51.100.0/24\n\xff")

    assert result.outcome is ParseOutcome.failed
    assert result.physical_line_count == 0
    assert result.cidrs == ()
    assert result.invalid_count == 0
    assert result.duplicate_count == 0
    assert result.invalid_line_diagnostics[0].line_number is None
    assert result.invalid_line_diagnostics[0].reason == "Feed is not valid UTF-8"
