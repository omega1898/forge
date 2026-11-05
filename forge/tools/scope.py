"""Target scope for testing tools (ticket F23): targets stay within the grant.

A testing tool (an HTTP probe, a port check, a load generator) may only
touch targets the trusted user granted. Scope checks are a classic place
for bypasses, so this module normalizes first and refuses anything
ambiguous instead of guessing:

- URL userinfo (`http://allowed@evil/`) is refused: it hides the real host;
- only canonical dotted IPv4 and bracketed IPv6 are accepted as addresses;
  decimal (`2130706433`), hexadecimal (`0x7f.1`), octal (`0177.0.0.1`) and
  shortened forms are refused, because resolvers disagree about them;
- IPv4-mapped IPv6 (`::ffff:10.0.0.5`) is checked as the IPv4 address;
- non-ASCII host names are refused (homographs); punycode must be listed
  exactly;
- a trailing dot, empty labels, control characters, whitespace and
  backslashes are refused;
- a host name must be listed, AND every address it resolves to must lie in
  the granted networks when networks are granted. The resolved addresses
  are returned so the tool connects to exactly those (no second lookup for
  DNS rebinding to exploit);
- the port and scheme must be granted; default ports are made explicit.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from pydantic import Field

from forge.contracts.base import StrictModel
from forge.contracts.errors import ErrorCode, ForgeError

DEFAULT_PORTS = {"http": 80, "https": 443}
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_NUMERIC_HOST = re.compile(r"^[0-9a-fx.]+$")


class TargetScope(StrictModel):
    hosts: list[str] = Field(default_factory=list)          # exact names, or "*.example.test" for subdomains
    networks: list[str] = Field(default_factory=list)       # CIDR blocks
    ports: list[int] = Field(default_factory=list)
    schemes: list[str] = Field(default_factory=lambda: ["http", "https"])


class Target(StrictModel):
    scheme: str
    host: str
    port: int
    addresses: list[str]
    path: str


def _refuse(reason: str) -> ForgeError:
    return ForgeError(ErrorCode.PERMISSION_DENIED, f"target refused: {reason}")


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _in_networks(address, scope: TargetScope) -> bool:  # noqa: ANN001
    return any(address in ipaddress.ip_network(cidr, strict=False) for cidr in scope.networks)


def _host_listed(host: str, scope: TargetScope) -> bool:
    for allowed in scope.hosts:
        allowed = allowed.lower()
        if allowed.startswith("*.") and host.endswith(allowed[1:]) and host != allowed[2:]:
            return True
        if host == allowed:
            return True
    return False


def check_target(target: str, scope: TargetScope, *,
                 resolver: Callable[[str], list[str]] | None = None) -> Target:
    if not isinstance(target, str) or not target or len(target) > 2048:
        raise _refuse("empty or too long")
    if any(ord(c) < 33 or ord(c) == 127 for c in target) or "\\" in target:
        raise _refuse("contains whitespace, control characters or backslashes")
    if not target.isascii():
        raise _refuse("contains non-ASCII characters; use the punycode form")
    try:
        parts = urlsplit(target)
    except ValueError:
        raise _refuse("bracketed host is not an IPv6 address") from None
    scheme = parts.scheme.lower()
    if scheme not in scope.schemes:
        raise _refuse(f"scheme {scheme or '(none)'!r} is not granted")
    if "@" in parts.netloc:
        raise _refuse("the URL carries user information, which hides the real host")
    raw_host = parts.netloc.rsplit(":", 1)[0] if not parts.netloc.startswith("[") else parts.netloc.split("]")[0] + "]"
    try:
        host = (parts.hostname or "").lower()
    except ValueError:
        raise _refuse("bracketed host is not an IPv6 address") from None
    if not host:
        raise _refuse("no host")
    try:
        port = parts.port or DEFAULT_PORTS.get(scheme)
    except ValueError:
        raise _refuse("the port is not a number in range") from None
    if port is None or port not in scope.ports:
        raise _refuse(f"port {port} is not granted")
    address = _address(host)
    if raw_host.startswith("[") and address is None:
        raise _refuse("bracketed host is not an IPv6 address")
    if address is not None:
        # ipaddress accepts only canonical dotted IPv4; other numeric forms fall through to the check below.
        if not _in_networks(address, scope):
            raise _refuse(f"{address} is outside the granted networks")
        return Target(scheme=scheme, host=str(address), port=port, addresses=[str(address)], path=parts.path or "/")
    if _NUMERIC_HOST.match(host) and any(ch.isdigit() for ch in host):
        raise _refuse("numeric host in a non-canonical form (decimal, hex, octal or shortened)")
    labels = host.split(".")
    if host.endswith(".") or any(not _LABEL.match(label) for label in labels):
        raise _refuse("host name is not a plain, fully written DNS name")
    if not _host_listed(host, scope):
        raise _refuse(f"host {host} is not granted")
    resolve = resolver or _system_resolver
    addresses = sorted(set(resolve(host)))
    if not addresses:
        raise _refuse(f"{host} does not resolve")
    if scope.networks:
        outside = [a for a in addresses if not _in_networks(_address(a), scope)]
        if outside:
            raise _refuse(f"{host} resolves outside the granted networks: {outside}")
    return Target(scheme=scheme, host=host, port=port, addresses=addresses, path=parts.path or "/")


def _system_resolver(host: str) -> list[str]:
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]
    except OSError:
        return []
