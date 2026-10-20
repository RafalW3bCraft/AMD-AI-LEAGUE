"""Stage 3: FETCH + NAVIGATE.

Bounded HTTP fetcher with:
- aiohttp + gzip/deflate
- per-request timeout
- redirect cap (5)
- HTML stripping for downstream stages
- in-page link extraction with same-origin preference
- prompt-injection flagging (strip <script>, <style>, HTML comments,
  meta-refresh, hidden inputs)

The fetcher is intentionally NOT a headless browser by default. Match 4
asks for *accurate retrieval*, not browser automation. JS-only pages will
return the server-rendered HTML or a refusal — both honest.
"""

from __future__ import annotations

import asyncio
import gzip
import html
import io
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urljoin, urlparse, urldefrag

import aiohttp

from .config import AgentConfig
from .safety import UnsafeUrlError, check_url_or_raise
from .urlnorm import normalize_url, same_registrable_domain, canonical_key
from .deadline import Deadline

log = logging.getLogger("match4.fetch")


@dataclass
class FetchedPage:
    url: str
    final_url: str
    status: int
    title: str = ""
    text: str = ""
    html: str = ""
    links: list[str] = field(default_factory=list)
    hop: int = 0
    elapsed_ms: int = 0
    ok: bool = True
    error: str | None = None

    @property
    def domain(self) -> str:
        return urlparse(self.final_url or self.url).netloc.lower()


_TAG_RE = re.compile(r"<[^>]+>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.S | re.I)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.S | re.I)
_NOSCRIPT_RE = re.compile(r"<noscript[^>]*>.*?</noscript>", re.S | re.I)
_META_REFRESH_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]*content=["\']?\d+\s*;\s*url=([^"\']+)',
    re.I,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_WS_RE = re.compile(r"\s+")
_HREF_RE = re.compile(r"""<a[^>]+href=["']([^"']+)["']""", re.I)
# Match any element whose `style=` contains `display:none` or
# `visibility:hidden`, or any element with the HTML5 `hidden` attribute,
# and drop the entire element including its content.
_HIDDEN_RE = re.compile(
    r"<(?:[a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(?:"
    r"style\s*=\s*[\"'][^\"']*\b(?:display\s*:\s*none|visibility\s*:\s*hidden)\b[^\"']*[\"']"
    r"|\bhidden\b"
    r")[^>]*>.*?</(?:[a-zA-Z][a-zA-Z0-9]*)>",
    re.S | re.I,
)
# Self-closing hidden tags (e.g. <img style="display:none">)
_HIDDEN_VOID_RE = re.compile(
    r"<(?:[a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(?:"
    r"style\s*=\s*[\"'][^\"']*\b(?:display\s*:\s*none|visibility\s*:\s*hidden)\b[^\"']*[\"']"
    r"|\bhidden\b"
    r")[^>]*/?>",
    re.I,
)

# Prompt-injection patterns — flagged, not auto-trusted.
_INJECTION_PATTERNS = [
    re.compile(r"ignore (?:previous|all|above) instructions?", re.I),
    re.compile(r"disregard (?:previous|all|above)", re.I),
    re.compile(r"new instructions?:", re.I),
    re.compile(r"you are now", re.I),
    re.compile(r"system prompt", re.I),
]


class Fetcher:
    """Async fetcher with shared connection pool, security checks, and
    bounded resource usage.

    Security defaults (matching Match 4 best practice):
      - allow only http(s) schemes
      - reject userinfo in URLs (no implicit credential forwarding)
      - reject private / loopback / link-local destinations unless
        ``allow_local_network=True`` is set on the config
      - cap decompressed body size at ``cfg.max_html_bytes``
      - cap per-page text size at ``cfg.max_text_chars``
      - cap redirect hops at ``cfg.max_redirects``
      - global concurrency semaphore on ``cfg.max_concurrent_fetches``
    """

    def __init__(self, cfg: AgentConfig, deadline: "Deadline | None" = None) -> None:
        self.cfg = cfg
        self._session: aiohttp.ClientSession | None = None
        self._sem: asyncio.Semaphore | None = None
        self._deadline = deadline  # optional, set per-call via fetch()

    def _get_connector(self) -> aiohttp.TCPConnector | None:
        """Build a connector that pins DNS resolution to validate every
        address we are about to connect to.

        Returns ``None`` if DNS pinning is unavailable on this Python
        (in which case the URL-level safety check + redirect-history
        audit remain in effect).
        """
        if not getattr(self, "_use_dns_pin", True):
            return None
        try:
            from aiohttp.resolver import ThreadedResolver
        except ImportError:  # pragma: no cover
            return None

        # Force IPv4 resolution to avoid IPv4-mapped IPv6 confusion.
        # (We still accept AAAA records, but the pin verifies the IP
        # family we will actually connect to.)
        resolver = ThreadedResolver()

        async def _resolve(host: str, port: int = 0, *args, **kwargs):
            # aiohttp ≥3.10 calls `await self._resolver.resolve(...)` and then
            # passes the result through `_convert_hosts_to_addr_infos()`, which
            # expects ``list[ResolveResult]`` — NOT the legacy
            # ``socket.getaddrinfo`` 5-tuple format. Returning the raw
            # getaddrinfo tuples crashes aiohttp with
            # ``TypeError: tuple indices must be integers or slices, not str``
            # inside `_convert_hosts_to_addr_infos` because it does
            # ``hinfo["host"]`` on a tuple.
            import socket as _socket
            from aiohttp.abc import ResolveResult
            infos = _socket.getaddrinfo(host, port)
            # Re-validate every resolved IP against the safety blocklist
            # at the moment we are about to connect. If ANY resolves to
            # a private/mapped/disallowed address, we refuse the whole
            # resolution — aiohttp will surface this as a connection
            # error rather than silently connecting to the wrong IP.
            from .safety import _ip_in_blocked
            hosts: list[ResolveResult] = []
            for info in infos:
                # getaddrinfo returns 5-tuples:
                #   (family, type, proto, canonname, sockaddr)
                if not isinstance(info, tuple) or len(info) < 5:
                    continue
                family, _type, proto, _canon, sockaddr = info
                if not sockaddr:
                    continue
                addr = sockaddr[0]
                if not isinstance(addr, str):
                    continue
                if _ip_in_blocked(addr):
                    raise OSError(
                        f"refused to resolve {host} → blocked address {addr}"
                    )
                resolved_port = (
                    sockaddr[1] if len(sockaddr) > 1 and isinstance(sockaddr[1], int)
                    else port
                )
                hosts.append(ResolveResult(
                    hostname=host,
                    host=addr,
                    port=resolved_port,
                    family=family,
                    proto=proto,
                    flags=0,
                ))
            return hosts

        try:
            resolver.resolve = _resolve  # type: ignore[method-assign]
        except Exception:  # pragma: no cover
            return None

        return aiohttp.TCPConnector(
            resolver=resolver,
            force_close=False,
            enable_cleanup_closed=True,
            limit=self.cfg.max_concurrent_fetches * 2,
        )

    async def _ensure(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = self._get_connector()
            if connector is not None:
                self._session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=aiohttp.ClientTimeout(total=self.cfg.fetch_timeout_s),
                    headers={
                        "User-Agent": self.cfg.user_agent,
                        "Accept-Encoding": "gzip, deflate",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
            else:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.cfg.fetch_timeout_s),
                    headers={
                        "User-Agent": self.cfg.user_agent,
                        "Accept-Encoding": "gzip, deflate",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
        return self._session

    def _get_sem(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.cfg.max_concurrent_fetches)
        return self._sem

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def fetch(
        self, url: str, deadline: "Deadline | None" = None,
    ) -> FetchedPage | None:
        """Fetch a URL with all safety + resource guards.

        ``deadline`` overrides the fetcher-level deadline for this call;
        if neither is set, falls back to the fetcher's own deadline.
        """
        # Resolve "effective" deadline and timeout.
        eff_deadline = deadline or self._deadline
        timeout_s = self.cfg.fetch_timeout_s
        if eff_deadline is not None:
            timeout_s = eff_deadline.with_cap(self.cfg.fetch_timeout_s)
            if eff_deadline.exceeded():
                return FetchedPage(
                    url=url, final_url=url, status=0,
                    ok=False, error="deadline",
                )

        # Pre-flight: reject unsafe URLs before even opening a socket.
        try:
            check_url_or_raise(url, allow_local=self.cfg.allow_local_network)
        except UnsafeUrlError as exc:
            return FetchedPage(
                url=url, final_url=url, status=0,
                ok=False, error=f"unsafe:{exc}",
            )

        sem = self._get_sem()
        async with sem:
            return await self._fetch_inner(url, timeout_s)

    async def _fetch_inner(self, url: str, timeout_s: float) -> FetchedPage:
        t0 = time.monotonic()
        page = FetchedPage(url=url, final_url=url, status=0)
        try:
            session = await self._ensure()
            client_timeout = aiohttp.ClientTimeout(total=timeout_s or 1.0)
            async with session.get(
                url,
                allow_redirects=True,
                max_redirects=self.cfg.max_redirects,
                timeout=client_timeout,
            ) as resp:
                # Safety check on EVERY redirect target. aiohttp exposes the
                # full redirect history on ``resp.history``; we audit each
                # intermediate URL against the blocklist. If any of them
                # points at a private / loopback / mapped address we treat
                # the whole chain as unsafe — even though aiohttp has
                # already opened the socket, the bytes never reached the
                # caller because we abort before reading the body.
                history = list(getattr(resp, "history", []) or [])
                for hop in history:
                    hop_url = str(getattr(hop, "url", "") or getattr(hop, "real_url", ""))
                    if not hop_url:
                        continue
                    try:
                        check_url_or_raise(
                            hop_url,
                            allow_local=self.cfg.allow_local_network,
                        )
                    except UnsafeUrlError as exc:
                        page.ok = False
                        page.error = f"unsafe-redirect:{exc}"
                        page.elapsed_ms = int((time.monotonic() - t0) * 1000)
                        return page
                # Final URL check (handles non-redirect cases too).
                if str(resp.url) != url:
                    try:
                        check_url_or_raise(
                            str(resp.url),
                            allow_local=self.cfg.allow_local_network,
                        )
                    except UnsafeUrlError as exc:
                        page.ok = False
                        page.error = f"unsafe-redirect:{exc}"
                        page.elapsed_ms = int((time.monotonic() - t0) * 1000)
                        return page
                page.status = resp.status
                page.final_url = str(resp.url)
                if resp.status >= 400:
                    page.ok = False
                    page.error = f"http {resp.status}"
                    return page
                # Read up to max_html_bytes + 1 to detect overflow.
                cap = max(1024, self.cfg.max_html_bytes)
                raw = await resp.content.read(cap + 1)
                if len(raw) > cap:
                    log.warning("fetch %s body truncated at %d bytes", url, cap)
                    raw = raw[:cap]
                ctype = resp.headers.get("Content-Type", "").lower()
                if "html" not in ctype and "xml" not in ctype and "json" not in ctype:
                    # Non-HTML/JSON response — store as text only.
                    try:
                        page.text = raw.decode("utf-8", errors="replace")[
                            : self.cfg.max_text_chars
                        ]
                    except Exception:
                        page.text = ""
                    return page
                # aiohttp already decompressed if Content-Encoding was set
                # and it understood it; only fall back to gzip for raw
                # double-encoded bodies.
                if resp.headers.get("Content-Encoding", "").lower() == "gzip" and not raw.startswith(b"<"):
                    try:
                        raw = gzip.decompress(raw)
                    except OSError:
                        pass
                try:
                    html_text = raw.decode("utf-8", errors="replace")
                except Exception:
                    html_text = raw.decode("latin-1", errors="replace")
                page.html = html_text
                page.title = self._extract_title(html_text)
                page.text = self._html_to_text(html_text)
                page.links = self.extract_links_from(html_text, page.final_url)
        except asyncio.TimeoutError:
            page.ok = False
            page.error = "timeout"
        except aiohttp.ClientError as exc:
            page.ok = False
            page.error = f"client:{type(exc).__name__}"
        except Exception as exc:  # noqa: BLE001
            page.ok = False
            page.error = f"{type(exc).__name__}:{exc}"
        finally:
            page.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return page

    # -------- helpers --------

    def extract_links(self, page: FetchedPage) -> list[str]:
        return list(page.links)

    @staticmethod
    def extract_links_from(html_text: str, base_url: str) -> list[str]:
        """Extract in-page links with anchor text for navigation relevance.

        Returns URLs in the order: same-origin content links first (sorted
        by relevance to common intents), then off-site. Filters out
        obvious tracking / social / CDN / auth paths.
        """
        out: list[str] = []
        seen: set[str] = set()
        base_domain = urlparse(base_url).netloc.lower()
        for m in _HREF_RE.finditer(html_text):
            href = html.unescape(m.group(1)).strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(base_url, href)
            absolute, _ = urldefrag(absolute)
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.netloc.lower() == base_domain:
                pass
            elif not _is_likely_content_link(absolute):
                continue
            norm = normalize_url(absolute)
            if norm in seen:
                continue
            seen.add(norm)
            out.append(absolute)
        # Same-origin first, then off-site, cap at 25 per page.
        same = [u for u in out if urlparse(u).netloc.lower() == base_domain]
        off = [u for u in out if urlparse(u).netloc.lower() != base_domain]
        return (same + off)[:25]

    @staticmethod
    def extract_anchor_texts(html_text: str, base_url: str) -> list[tuple[str, str]]:
        """Extract ``(href, anchor_text)`` tuples from anchor tags.

        Used by Agent navigation logic to choose links whose anchor text
        matches the query intent (e.g. ``<a href="/contact">Contact us</a>``
        is a strong match for the "contact" intent).
        """
        out: list[tuple[str, str]] = []
        for m in re.finditer(
            r'<a\b[^>]*\bhref=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>',
            html_text, flags=re.S | re.I,
        ):
            href = html.unescape(m.group(1)).strip()
            text = _strip_anchor_text(m.group(2))
            if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(base_url, href)
            absolute, _ = urldefrag(absolute)
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https"):
                continue
            out.append((absolute, text))
        return out

    @staticmethod
    def _extract_title(html_text: str) -> str:
        m = _TITLE_RE.search(html_text)
        if not m:
            return ""
        return _WS_RE.sub(" ", _TAG_RE.sub(" ", m.group(1))).strip()[:200]

    @staticmethod
    def _html_to_text(html_text: str) -> str:
        """Strip scripts/styles/comments/noscript/hidden, then tags."""
        if not html_text:
            return ""
        # Strip dangerous / non-content blocks first.
        s = _COMMENT_RE.sub(" ", html_text)
        s = _SCRIPT_RE.sub(" ", s)
        s = _STYLE_RE.sub(" ", s)
        s = _NOSCRIPT_RE.sub(" ", s)
        s = _HIDDEN_RE.sub(" ", s)
        s = _HIDDEN_VOID_RE.sub(" ", s)
        # Drop meta-refresh redirect hints (prompt-injection vector).
        s = _META_REFRESH_RE.sub(" ", s)
        # Convert <br>, </p>, headings, list items, table cells to newlines.
        s = re.sub(
            r"<(br|/p|/div|/li|/tr|/h[1-6])[^>]*>", "\n", s, flags=re.I
        )
        s = re.sub(r"</td[^>]*>", "\t", s, flags=re.I)
        # Strip remaining tags.
        s = _TAG_RE.sub(" ", s)
        # Decode entities and normalize whitespace.
        s = html.unescape(s)
        s = _WS_RE.sub(" ", s)
        s = re.sub(r" *\n *", "\n", s)
        return s.strip()

    @staticmethod
    def flag_injection(html_text: str) -> list[str]:
        """Return a list of prompt-injection patterns detected in raw HTML
        (before stripping). Pure signal for the trail."""
        flags: list[str] = []
        if not html_text:
            return flags
        for pat in _INJECTION_PATTERNS:
            for m in pat.finditer(html_text):
                flags.append(m.group(0))
                if len(flags) >= 5:
                    return flags
        return flags


def _is_likely_content_link(url: str) -> bool:
    """Heuristic: skip obvious tracking / social / auth / CDN paths."""
    low = url.lower()
    skip = (
        "facebook.com", "twitter.com", "x.com", "linkedin.com",
        "instagram.com", "youtube.com", "pinterest.com", "tiktok.com",
        "doubleclick.net", "googleadservices.com", "googletagmanager",
        "/login", "/signup", "/sign-in", "/sign-up", "/auth",
        ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg",
        ".ico", ".woff", ".woff2", ".ttf",
    )
    return not any(s in low for s in skip)


def _strip_anchor_text(html: str) -> str:
    """Strip HTML tags and collapse whitespace inside anchor text."""
    s = _TAG_RE.sub(" ", html)
    from html import unescape as _unescape
    s = _unescape(s)
    s = _WS_RE.sub(" ", s).strip()
    return s[:200]