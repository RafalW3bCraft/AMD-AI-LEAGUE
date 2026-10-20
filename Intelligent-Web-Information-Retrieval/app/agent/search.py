"""Stage 2: SEARCH — provider chain with automatic fallback.

Primary: DuckDuckGo HTML (keyless, no signup).
Fallback: deterministic local results from a small "stub" provider for tests
and offline runs.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from dataclasses import dataclass
from typing import Iterable, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp

from .config import AgentConfig

log = logging.getLogger("match4.search")


@dataclass
class Hit:
    url: str
    title: str
    snippet: str
    source: str  # which provider returned it
    score: float = 0.0


class SearchProvider(Protocol):
    name: str

    async def search(
        self, query: str, limit: int = 5,
        deadline: object = None,
    ) -> list[Hit]: ...

    async def search_all(
        self, queries: Iterable[str], limit: int = 5,
        deadline: object = None,
    ) -> list[Hit]:
        """Default: iterate queries, de-duplicate hits, and stop early
        once the deadline is exhausted (if supplied)."""
        seen: set[str] = set()
        out: list[Hit] = []
        for q in queries:
            if deadline is not None and getattr(deadline, "exceeded", lambda: False)():
                break
            try:
                hits = await self.search(q, limit=limit, deadline=deadline)
            except TypeError:
                # Backwards-compat: provider without deadline support.
                hits = await self.search(q, limit=limit)
            except Exception as exc:  # noqa: BLE001
                log.warning("provider %s failed for %r: %s", self.name, q, exc)
                continue
            for h in hits:
                if h.url in seen:
                    continue
                seen.add(h.url)
                out.append(h)
            if deadline is not None and getattr(deadline, "exceeded", lambda: False)():
                break
        return out


class DuckDuckGoProvider:
    """DuckDuckGo HTML (no key required). Falls back to lite.duckduckgo.com."""

    name = "duckduckgo"

    async def search_all(
        self, queries: Iterable[str], limit: int = 5,
        deadline: object = None,
    ) -> list[Hit]:
        """Iterate queries, de-duplicate hits across variants.

        If ``deadline`` is supplied (with ``.exceeded()``/``.with_cap()``
        methods), each per-endpoint attempt is bounded by the remaining
        budget so a slow upstream cannot exhaust the query deadline.
        """
        seen: set[str] = set()
        out: list[Hit] = []
        for q in queries:
            if deadline is not None and getattr(deadline, "exceeded", lambda: False)():
                break
            try:
                hits = await self.search(q, limit=limit, deadline=deadline)
            except TypeError:
                hits = await self.search(q, limit=limit)
            except Exception as exc:  # noqa: BLE001
                log.warning("provider %s failed for %r: %s", self.name, q, exc)
                continue
            for h in hits:
                if h.url in seen:
                    continue
                seen.add(h.url)
                out.append(h)
            if deadline is not None and getattr(deadline, "exceeded", lambda: False)():
                break
        return out

    ENDPOINTS = [
        "https://html.duckduckgo.com/html/",   # POST form
        "https://lite.duckduckgo.com/lite/",   # GET fallback
    ]

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async def search(
        self, query: str, limit: int = 5, deadline: object = None,
    ) -> list[Hit]:
        """Run all endpoints in order. Fall through on empty results, not
        just on exceptions. Returns [] if every endpoint produced no hits.

        Each endpoint's network timeout is capped by ``deadline`` (when
        supplied) so a slow upstream cannot consume the entire query
        budget.
        """
        last_exc: Exception | None = None
        for endpoint in self.ENDPOINTS:
            if deadline is not None and getattr(deadline, "exceeded", lambda: False)():
                break
            try:
                if endpoint.endswith("html/"):
                    hits = await self._search_post(endpoint, query, limit, deadline)
                else:
                    hits = await self._search_get(endpoint, query, limit, deadline)
                if hits:
                    return hits
                log.info("ddg endpoint %s returned 0 results; trying next",
                         endpoint)
                continue
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                log.warning("ddg endpoint %s failed: %s", endpoint, exc)
                continue
        if last_exc:
            log.info("ddg: all endpoints failed for %r (last: %s)",
                     query, last_exc)
        return []

    @staticmethod
    def _cap_timeout(default_s: float, deadline: object) -> float:
        """Compute the per-endpoint network timeout.

        If a deadline is supplied, the per-call timeout is the minimum of
        the provider default and the deadline's remaining budget (minus
        the reserve). Otherwise the provider default applies.
        """
        if deadline is None:
            return default_s
        cap = getattr(deadline, "with_cap", None)
        if callable(cap):
            try:
                capped = float(cap(default_s))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return default_s
            return float(max(0.5, capped))
        return default_s

    async def _search_post(
        self, endpoint: str, query: str, limit: int,
        deadline: object = None,
    ) -> list[Hit]:
        to = self._cap_timeout(8.0, deadline)
        timeout = aiohttp.ClientTimeout(total=to)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            data = {"q": query, "kl": "us-en"}
            async with session.post(
                endpoint, data=data, headers=self.HEADERS
            ) as resp:
                resp.raise_for_status()
                body = await resp.text()
        return _parse_ddg_html(body, limit, source=self.name)

    async def _search_get(
        self, endpoint: str, query: str, limit: int,
        deadline: object = None,
    ) -> list[Hit]:
        to = self._cap_timeout(8.0, deadline)
        timeout = aiohttp.ClientTimeout(total=to)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            params = {"q": query, "kl": "us-en"}
            async with session.get(
                endpoint, params=params, headers=self.HEADERS
            ) as resp:
                resp.raise_for_status()
                body = await resp.text()
        return _parse_ddg_lite(body, limit, source=self.name)


class StubProvider:
    """Offline-safe provider used for tests and when network is unavailable."""

    name = "stub"

    def __init__(self, corpus: list[Hit] | None = None,
                 sleep_s: float = 0.0) -> None:
        self.corpus = corpus or []
        self.sleep_s = float(sleep_s)

    async def search(
        self, query: str, limit: int = 5, deadline: object = None,
    ) -> list[Hit]:
        q_tokens = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 2}
        scored: list[Hit] = []
        for hit in self.corpus:
            text = (hit.title + " " + hit.snippet + " " + hit.url).lower()
            overlap = sum(1 for t in q_tokens if t in text)
            if overlap == 0:
                continue
            scored.append(
                Hit(
                    url=hit.url,
                    title=hit.title,
                    snippet=hit.snippet,
                    source=self.name,
                    score=overlap / max(1, len(q_tokens)),
                )
            )
        scored.sort(key=lambda h: h.score, reverse=True)
        # Optional sleep for deadline tests. Honours deadline budget if
        # supplied.
        if self.sleep_s > 0:
            remaining: float = self.sleep_s
            if deadline is not None:
                cap = getattr(deadline, "with_cap", None)
                if callable(cap):
                    try:
                        remaining = float(cap(self.sleep_s))  # type: ignore[arg-type]
                    except (TypeError, ValueError):
                        remaining = self.sleep_s
            await asyncio.sleep(max(0.0, remaining))
        return scored[:limit]

    async def search_all(
        self, queries: Iterable[str], limit: int = 5,
        deadline: object = None,
    ) -> list[Hit]:
        seen: set[str] = set()
        out: list[Hit] = []
        for q in queries:
            if deadline is not None and getattr(deadline, "exceeded", lambda: False)():
                break
            try:
                hits = await self.search(q, limit=limit, deadline=deadline)
            except Exception as exc:  # noqa: BLE001
                log.warning("stub provider failed for %r: %s", q, exc)
                continue
            for h in hits:
                if h.url in seen:
                    continue
                seen.add(h.url)
                out.append(h)
            if deadline is not None and getattr(deadline, "exceeded", lambda: False)():
                break
        return out


def build_search_chain(cfg: AgentConfig) -> SearchProvider:
    """Return the primary provider. The chain-fallback behavior lives in
    Agent.run, which catches per-provider errors and tries the next one
    (so we can compose multiple providers cleanly)."""
    if cfg.search_provider == "stub":
        return StubProvider(corpus=cfg.stub_corpus)
    return DuckDuckGoProvider()


# --- HTML parsing for DDG -------------------------------------------------

_HIT_RE_HTML = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'.*?<a[^>]*class="result__snippet"[^>]*>(?P<snip>.*?)</a>',
    re.S | re.I,
)
_HIT_RE_LITE = re.compile(
    r'<a[^>]+class="result-link"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'.*?<td[^>]*class="result-snippet"[^>]*>(?P<snip>.*?)</td>',
    re.S | re.I,
)


def _parse_ddg_html(body: str, limit: int, source: str) -> list[Hit]:
    hits: list[Hit] = []
    for m in _HIT_RE_HTML.finditer(body):
        url = _normalize_ddg_url(html.unescape(m.group("url")))
        if not url.startswith(("http://", "https://")):
            continue
        title = _strip_tags(html.unescape(m.group("title")))
        snip = _strip_tags(html.unescape(m.group("snip")))
        hits.append(Hit(url=url, title=title, snippet=snip, source=source))
        if len(hits) >= limit:
            break
    return hits


def _parse_ddg_lite(body: str, limit: int, source: str) -> list[Hit]:
    hits: list[Hit] = []
    for m in _HIT_RE_LITE.finditer(body):
        url = html.unescape(m.group("url"))
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith(("http://", "https://")):
            continue
        title = _strip_tags(html.unescape(m.group("title")))
        snip = _strip_tags(html.unescape(m.group("snip")))
        hits.append(Hit(url=url, title=title, snippet=snip, source=source))
        if len(hits) >= limit:
            break
    return hits


def _normalize_ddg_url(raw: str) -> str:
    """DDG /html/ wraps result URLs in a redirect like
    //duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2F&... — unwrap."""
    if "uddg=" in raw:
        try:
            qs = parse_qs(urlparse("http://x" + raw if raw.startswith("//") else raw).query)
            target = qs.get("uddg", [raw])[0]
            return unquote(target)
        except Exception:  # noqa: BLE001
            return raw
    return raw


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(s: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", s)).strip()


# Combined helper used by Agent.run for collecting across variants.
async def search_all_via(
    providers: list[SearchProvider],
    queries: Iterable[str],
    limit: int,
) -> list[Hit]:
    """Round-robin: try each provider for each query, collecting hits.
    De-duplicated by URL, order preserved."""
    seen: set[str] = set()
    out: list[Hit] = []
    for q in queries:
        for prov in providers:
            try:
                hits = await prov.search(q, limit=limit)
            except Exception as exc:  # noqa: BLE001
                log.warning("provider %s failed for %r: %s", prov.name, q, exc)
                continue
            for h in hits:
                if h.url in seen:
                    continue
                seen.add(h.url)
                out.append(h)
                if len(out) >= limit * 3:
                    return out
    return out