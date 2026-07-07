from ipaddress import IPv4Network, ip_network


class CidrValidationError(ValueError):
    pass


def parse_ipv4_cidr(value: str) -> IPv4Network:
    try:
        network = ip_network(value, strict=True)
    except ValueError as exc:
        if _has_host_bits(value):
            canonical = ip_network(value, strict=False)
            raise CidrValidationError(
                f"CIDR has host bits set; canonical network is {canonical}"
            ) from exc
        raise CidrValidationError("Invalid CIDR") from exc

    if not isinstance(network, IPv4Network):
        raise CidrValidationError("IPv6 CIDRs are not supported")
    return network


def reject_reserved(net: IPv4Network) -> None:
    if net == IPv4Network("0.0.0.0/0") or net.subnet_of(IPv4Network("0.0.0.0/8")):
        raise CidrValidationError("Reserved CIDR range is not allowed")


def is_subnet(target: IPv4Network, container: IPv4Network) -> bool:
    return target.subnet_of(container)


def _has_host_bits(value: str) -> bool:
    try:
        strict_network = ip_network(value, strict=True)
        return not isinstance(strict_network, IPv4Network)
    except ValueError:
        try:
            return isinstance(ip_network(value, strict=False), IPv4Network)
        except ValueError:
            return False
