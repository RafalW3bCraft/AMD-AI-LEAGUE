"""URL / DNS security helpers — SSRF defenses.

Match 4 fetches untrusted URLs: start URLs supplied by the user, links
extracted from arbitrary web pages, and HTTP redirects. Each of those
vectors can target loopback (127.0.0.1, ::1), private networks
(10/8, 192.168/16, 172.16/12), link-local (169.254/16), the cloud
metadata service (169.254.169.254), or userinfo-bearing URLs that try to
trick the fetcher into sending credentials.

The helpers in this module:

- ``is_safe_url`` — scheme allowlist + reject userinfo + reject private
  IP literals (including IPv4-mapped IPv6 forms like ``::ffff:127.0.0.1``),
  UNLESS ``allow_local_network=True`` is set in the test harness.
- ``resolve_and_check`` — DNS-resolve the host and re-check each
  returned address against the same private-IP blocklist. This is the
  defense against DNS-rebinding-style attacks where the hostname resolves
  to an internal IP.

The intent is NOT to be a fully featured SSRF library — it is the
minimum defense needed to make a literal ``--start-url http://127.0.0.1``
or ``--start-url http://169.254.169.254/latest/meta-data/`` fail
immediately rather than succeed.

Iteration-6 fixes:
- IPv4-mapped IPv6 (``::ffff:127.0.0.1`` etc.) is now collapsed to its
  embedded IPv4 and re-checked.
- ``socket.getaddrinfo`` already returns resolved addresses; we
  additionally accept the rare case of AAAA-only hosts where the only
  mapped form is the literal that came in.
- ``is_safe_url`` now rejects ``0.0.0.0`` (covered by 0.0.0.0/8).
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Iterable
from urllib.parse import urlsplit

# Private / reserved blocks we always block (unless explicitly allowed).
# IMPORTANT: keep this list comprehensive — it is the *single source of
# truth* for every IP-based destination check in the agent.
_BLOCKED_NETS = [
    ipaddress.ip_network("0.0.0.0/8"),          # "this" network / unspecified
    ipaddress.ip_network("10.0.0.0/8"),         # private RFC1918
    ipaddress.ip_network("100.64.0.0/10"),      # carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),        # loopback v4
    ipaddress.ip_network("169.254.0.0/16"),     # link-local + cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),      # private RFC1918
    ipaddress.ip_network("192.0.0.0/24"),       # IETF protocol assignments
    ipaddress.ip_network("192.168.0.0/16"),     # private RFC1918
    ipaddress.ip_network("198.18.0.0/15"),      # benchmarking
    ipaddress.ip_network("224.0.0.0/4"),        # multicast
    ipaddress.ip_network("240.0.0.0/4"),        # reserved
    ipaddress.ip_network("255.255.255.255/32"), # broadcast
    # IPv6
    ipaddress.ip_network("::/128"),             # unspecified v6
    ipaddress.ip_network("::1/128"),            # loopback v6
    ipaddress.ip_network("::ffff:0:0/96"),      # ALL IPv4-mapped IPv6
    ipaddress.ip_network("64:ff9b::/96"),       # IPv4/IPv6 translation
    ipaddress.ip_network("100::/64"),           # discard prefix
    ipaddress.ip_network("2001::/23"),          # IETF protocol
    ipaddress.ip_network("2001:db8::/32"),      # documentation
    ipaddress.ip_network("fc00::/7"),           # ULA v6
    ipaddress.ip_network("fe80::/10"),          # link-local v6
    ipaddress.ip_network("ff00::/8"),           # multicast v6
]


class UnsafeUrlError(ValueError):
    """Raised when a URL fails the safety check."""


def _normalize_ip(addr: str) -> str:
    """Normalize an IP literal: collapse IPv4-mapped IPv6 to its IPv4 form
    so that ``::ffff:127.0.0.1`` matches the IPv4 block 127.0.0.0/8.
    Returns the canonical string form (Python ``ip_address``).
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return addr
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    return str(ip)


def _ip_in_blocked(addr: str) -> bool:
    try:
        norm = _normalize_ip(addr)
        ip = ipaddress.ip_address(norm)
    except ValueError:
        return False
    for net in _BLOCKED_NETS:
        if ip in net:
            return True
    return False


def is_safe_url(url: str, *, allow_local: bool = False,
                blocked_schemes: Iterable[str] = ()) -> bool:
    """Return True if the URL is safe to fetch.

    Checks:
      - scheme is http or https (or in ``blocked_schemes`` inverse-list)
      - no embedded userinfo (user:pass@host)
      - host resolves to a public IP, OR ``allow_local=True``
      - host is not an IPv4-mapped IPv6 literal pointing at private space
    """
    if not url:
        return False
    try:
        parts = urlsplit(url.strip())
    except Exception:
        return False
    if parts.scheme.lower() in blocked_schemes:
        return False
    if parts.scheme.lower() not in ("http", "https"):
        return False
    if "@" in parts.netloc:
        # user:pass@host → reject; we never forward credentials.
        return False
    host = (parts.hostname or "").lower().strip(".")
    if not host:
        return False
    if allow_local:
        return True
    # If host is an IP literal, check directly.
    try:
        ipaddress.ip_address(host)
        if _ip_in_blocked(host):
            return False
        return True
    except ValueError:
        pass
    # Hostname → resolve and check each returned address.
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    for fam, *_rest, sockaddr in infos:
        addr = sockaddr[0]
        if not isinstance(addr, str):
            continue
        if _ip_in_blocked(addr):
            return False
    return True


def check_url_or_raise(url: str, *, allow_local: bool = False) -> None:
    """Raise UnsafeUrlError if not safe."""
    if not is_safe_url(url, allow_local=allow_local):
        raise UnsafeUrlError(f"unsafe URL: {url!r}")


def filter_safe_urls(urls: Iterable[str], *, allow_local: bool = False) -> list[str]:
    """Drop unsafe URLs from an iterable, preserving order."""
    out: list[str] = []
    for u in urls:
        if is_safe_url(u, allow_local=allow_local):
            out.append(u)
    return out