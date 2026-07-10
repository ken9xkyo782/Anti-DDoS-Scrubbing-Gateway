from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Address

from app.core.cidr import CidrValidationError, parse_ipv4_cidr

MAX_INVALID_LINE_DIAGNOSTICS = 20


class ParseOutcome(StrEnum):
    success = "success"
    partial = "partial"
    failed = "failed"


@dataclass(frozen=True)
class InvalidLineDiagnostic:
    line_number: int | None
    reason: str


@dataclass(frozen=True)
class ParseResult:
    physical_line_count: int
    cidrs: tuple[str, ...]
    invalid_count: int
    duplicate_count: int
    invalid_line_diagnostics: tuple[InvalidLineDiagnostic, ...]
    outcome: ParseOutcome

    @property
    def valid_distinct_count(self) -> int:
        return len(self.cidrs)


def parse_line_list(data: bytes) -> ParseResult:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ParseResult(
            physical_line_count=0,
            cidrs=(),
            invalid_count=0,
            duplicate_count=0,
            invalid_line_diagnostics=(
                InvalidLineDiagnostic(line_number=None, reason="Feed is not valid UTF-8"),
            ),
            outcome=ParseOutcome.failed,
        )

    cidrs: list[str] = []
    seen: set[str] = set()
    diagnostics: list[InvalidLineDiagnostic] = []
    invalid_count = 0
    duplicate_count = 0
    lines = text.splitlines()

    for line_number, raw_line in enumerate(lines, start=1):
        value = _strip_comment(raw_line).strip()
        if not value:
            continue

        try:
            canonical = _normalize_cidr(value)
        except CidrValidationError as exc:
            invalid_count += 1
            if len(diagnostics) < MAX_INVALID_LINE_DIAGNOSTICS:
                diagnostics.append(InvalidLineDiagnostic(line_number=line_number, reason=str(exc)))
            continue

        if canonical in seen:
            duplicate_count += 1
            continue
        seen.add(canonical)
        cidrs.append(canonical)

    outcome = _outcome(valid_distinct_count=len(cidrs), invalid_count=invalid_count)
    return ParseResult(
        physical_line_count=len(lines),
        cidrs=tuple(cidrs),
        invalid_count=invalid_count,
        duplicate_count=duplicate_count,
        invalid_line_diagnostics=tuple(diagnostics),
        outcome=outcome,
    )


def _strip_comment(line: str) -> str:
    comment_start = len(line)
    for delimiter in ("#", ";"):
        position = line.find(delimiter)
        if position != -1:
            comment_start = min(comment_start, position)
    return line[:comment_start]


def _normalize_cidr(value: str) -> str:
    if "/" not in value and _is_ipv4_address(value):
        value = f"{value}/32"

    network = parse_ipv4_cidr(value)
    if network.prefixlen == 0:
        raise CidrValidationError("CIDR /0 is not allowed")
    return str(network)


def _is_ipv4_address(value: str) -> bool:
    try:
        IPv4Address(value)
    except ValueError:
        return False
    return True


def _outcome(*, valid_distinct_count: int, invalid_count: int) -> ParseOutcome:
    if valid_distinct_count == 0:
        return ParseOutcome.failed
    if invalid_count > 0:
        return ParseOutcome.partial
    return ParseOutcome.success
