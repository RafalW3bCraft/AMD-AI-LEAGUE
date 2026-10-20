"""URL canonicalization and same-domain helpers.

Provides:
- ``normalize_url`` — strip fragment, lowercase host, normalize path.
- ``same_registrable_domain`` — return True if two URLs share the same
  eTLD+1 (best-effort, no external PSL; falls back to last-two-labels).
- ``strip_tracking_params`` — drop common tracking query params.
"""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "yclid",
    "_ga", "_gl", "ref", "ref_src", "ref_url",
})


def normalize_url(url: str, *, drop_tracking: bool = True) -> str:
    """Return a normalized form of ``url`` suitable for deduplication and
    domain checks. Lowercased scheme + host, fragment dropped, optional
    tracking-query-param strip."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except Exception:
        return url
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return url  # leave untouched; caller should reject
    netloc = parts.netloc.lower()
    # Strip default ports.
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = parts.path or "/"
    # Collapse duplicate slashes (except leading).
    path = re.sub(r"/{2,}", "/", path)
    # Drop trailing slash except for root.
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query = parts.query
    if drop_tracking and query:
        kept = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
                if k.lower() not in _TRACKING_PARAMS]
        query = urlencode(kept)
    return urlunsplit((scheme, netloc, path, query, ""))


def registrable_domain(host: str) -> str:
    """Best-effort eTLD+1 extraction. We don't ship the Public Suffix List;
    for our purposes (same-site scoping on the AMD challenge), matching by
    last-two-labels is a sane heuristic that handles com/org/gov/io/co
    but not complex multi-label public suffixes. The verifier only uses
    this for the page_key bucket count, not for security decisions.
    """
    host = (host or "").lower().strip(".")
    if not host:
        return ""
    # Strip port if present.
    if ":" in host:
        host = host.split(":", 1)[0]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    # Heuristic: if the second-to-last label is one of the common 2-part TLDs,
    # group three labels; else two.
    sld2 = {"co", "com", "org", "gov", "edu", "net", "ac", "mil", "int",
            "biz", "info", "name", "pro"}
    if len(parts) >= 3 and parts[-2] in sld2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def same_registrable_domain(a: str, b: str) -> bool:
    """True if both URLs share the same registrable domain."""
    try:
        ha = urlsplit(a).netloc.lower()
        hb = urlsplit(b).netloc.lower()
    except Exception:
        return False
    if not ha or not hb:
        return False
    return registrable_domain(ha) == registrable_domain(hb)


def canonical_key(url: str) -> str:
    """Domain+path-stem bucket key for dedup / agreement counting.

    Replaces digits in path segments with ``N`` so ``/products/123`` and
    ``/products/456`` fall into the same bucket — useful for paginated
    listings, but still keeps distinct pages distinguishable by stem.
    """
    if not url:
        return ""
    norm = normalize_url(url)
    try:
        parts = urlsplit(norm)
    except Exception:
        return norm
    host = parts.netloc
    path = re.sub(r"\d+", "N", parts.path or "")
    return f"{host}{path}"