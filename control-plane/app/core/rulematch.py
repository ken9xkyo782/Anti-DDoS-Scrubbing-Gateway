from dataclasses import dataclass

type PortRange = tuple[int | None, int | None]


class PortRangeError(ValueError):
    pass


@dataclass(frozen=True)
class RuleView:
    id: str
    protocol: str
    src_port: PortRange
    dst_port: PortRange


def validate_port_range(lo: int | None, hi: int | None) -> None:
    if lo is None and hi is None:
        return
    if lo is None or hi is None:
        raise PortRangeError("Port range must include both bounds or neither")
    if lo < 0 or hi > 65535 or lo > hi:
        raise PortRangeError("Port range must satisfy 0 <= lo <= hi <= 65535")


def rules_overlap(a: RuleView, b: RuleView) -> bool:
    return (
        _protocols_intersect(a.protocol, b.protocol)
        and _port_ranges_intersect(a.src_port, b.src_port)
        and _port_ranges_intersect(a.dst_port, b.dst_port)
    )


def find_overlaps(existing: list[RuleView], candidate: RuleView) -> list[RuleView]:
    return [rule for rule in existing if rules_overlap(rule, candidate)]


def _protocols_intersect(a: str, b: str) -> bool:
    return a == b or a == "any" or b == "any"


def _port_ranges_intersect(a: PortRange, b: PortRange) -> bool:
    validate_port_range(*a)
    validate_port_range(*b)
    if a == (None, None) or b == (None, None):
        return True
    a_lo, a_hi = a
    b_lo, b_hi = b
    if a_lo is None or a_hi is None or b_lo is None or b_hi is None:
        return False
    return a_lo <= b_hi and b_lo <= a_hi
