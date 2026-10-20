"""Match 4 agent — top-level orchestrator.

Implements the 5-stage pipeline documented in contracts/CONTRACT.md:

    Understand  ->  Search  ->  Fetch+Navigate  ->  Extract  ->  Verify

Deterministic by default (no LLM). Optional LLM hook via env var
MATCH4_USE_LLM=1 + MATCH4_LLM_ENDPOINT.

The agent exposes one async method:

    await Agent().run(query, start_urls=...) -> AgentResult

An AgentResult is JSON-serializable to the contract output shape.

Iteration 2 changes:
  - Deadline propagation (remaining budget visible to search/fetch/verify).
  - Start URLs are fetched FIRST and used as the primary evidence
    source; search is only consulted when start URL evidence is
    insufficient or unavailable.
  - Domain-scoped filtering of search hits (same registrable domain).
  - Entity-aware navigation (anchor text + intent-aware URL paths).
  - Entity-specific answer selection (the value must be associated with
    the asked entity in the same record/row/block).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any
from urllib.parse import urlparse

from .understand import understand
from .search import SearchProvider, DuckDuckGoProvider, build_search_chain
from .fetch import Fetcher, FetchedPage
from .extract import extract_candidates, Candidate
from .verify import verify_candidates
from .config import AgentConfig, load_config
from .deadline import Deadline
from .urlnorm import (
    normalize_url, same_registrable_domain, canonical_key,
)

log = logging.getLogger("match4.agent")


@dataclass
class TrailStep:
    url: str
    hop: int
    reason: str
    score: float = 0.0
    title: str = ""


@dataclass
class AgentResult:
    query: str
    answer: str = ""
    citations: list[str] = field(default_factory=list)
    confidence: float = 0.0
    trail: list[TrailStep] = field(default_factory=list)
    reasoning: str = ""
    intent: str = ""
    entities: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    pages_fetched: int = 0
    error: str | None = None

    def to_contract_json(self) -> dict[str, Any]:
        d = asdict(self)
        # Strip internal-only fields, keep contract-required + optional extras.
        d.pop("error", None)
        return {
            "answer": self.answer,
            "citations": list(self.citations),
            "confidence": round(float(self.confidence), 3),
            "trail": [asdict(t) for t in self.trail],
            "reasoning": self.reasoning,
            "intent": self.intent,
            "entities": list(self.entities),
            "elapsed_ms": self.elapsed_ms,
            "pages_fetched": self.pages_fetched,
        }


class Agent:
    """Deterministic web information retrieval agent."""

    def __init__(self, cfg: AgentConfig | None = None) -> None:
        self.cfg = cfg or load_config()
        self.search: SearchProvider = build_search_chain(self.cfg)
        self.fetcher = Fetcher(self.cfg)
        self._closed = False

    async def aclose(self) -> None:
        if not self._closed:
            await self.fetcher.aclose()
            self._closed = True

    async def __aenter__(self) -> "Agent":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def run(
        self,
        query: str,
        start_urls: list[str] | None = None,
    ) -> AgentResult:
        """Execute the 5-stage pipeline. Always returns an AgentResult
        (never raises); failures are recorded in `reasoning` / `error`."""
        deadline = Deadline(
            total_s=self.cfg.query_deadline_s,
            reserve_s=self.cfg.output_reserve_s,
        )
        t0 = time.monotonic()
        result = AgentResult(query=query)
        pages: list[FetchedPage] = []
        try:
            # STAGE 1: UNDERSTAND
            understanding = understand(query)
            result.intent = understanding.intent
            result.entities = list(understanding.entities)
            result.reasoning = (
                f"intent={understanding.intent}; "
                f"variants={len(understanding.query_variants)}; "
                f"entities={len(understanding.entities)}"
            )
            log.info(
                "understand: intent=%s variants=%d entities=%d",
                understanding.intent,
                len(understanding.query_variants),
                len(understanding.entities),
            )

            # STAGE 2 + 3 (split): normalize start URLs and fetch them
            # FIRST. Search is a fallback only when start URLs did not
            # yield a confident answer. This is the fix for the order
            # bug noted in the post-Hermes audit (P0-B).
            start_urls_list: list[str] = []
            start_domain: str | None = None
            if start_urls:
                start_urls_list = [u for u in (normalize_url(s) for s in start_urls) if u]
                if start_urls_list:
                    start_domain = urlparse(start_urls_list[0]).netloc.lower()

            pages, ordered = await self._fetch_pipeline(
                understanding, start_urls_list, deadline, result,
            )

            if not pages:
                # No pages at all (no start URLs AND search yielded nothing,
                # or every fetch failed before deadline). Return refusal.
                result.reasoning += "; no pages fetched"
                result.elapsed_ms = int((time.monotonic() - t0) * 1000)
                return result

            # STAGE 4: EXTRACT
            candidates = extract_candidates(
                pages=pages,
                intent=understanding.intent,
                entities=understanding.entities,
                raw_query=query,
                cfg=self.cfg,
            )

            # STAGE 5: VERIFY
            verified = verify_candidates(
                candidates=candidates,
                pages=pages,
                intent=understanding.intent,
                entities=understanding.entities,
                cfg=self.cfg,
            )

            if verified.best is None:
                result.reasoning += "; refused (no evidence)"
                result.elapsed_ms = int((time.monotonic() - t0) * 1000)
                return result

            best = verified.best
            result.answer = best.value
            result.citations = best.citations
            result.confidence = best.confidence
            result.reasoning = (
                f"{result.reasoning}; picked={best.kind} "
                f"support={best.support_count} agreement={best.agreement:.2f}"
            )

        except Exception as exc:  # never raise out of run()
            log.exception("agent.run failed")
            result.error = f"{type(exc).__name__}: {exc}"
            result.reasoning = (result.reasoning or "") + f"; error={exc}"
        finally:
            result.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return result

    async def _fetch_pipeline(
        self,
        understanding: Any,
        start_urls_list: list[str],
        deadline: Deadline,
        result: AgentResult,
    ) -> tuple[list[FetchedPage], list[tuple[int, TrailStep]]]:
        """Run STAGE 2 (search) + STAGE 3 (fetch+navigate).

        Order of operations (fix for P0-B / P0-E):
          1. Try start URLs FIRST and ONLY — bound by remaining deadline.
          2. If start URLs succeeded AND the fetched pages mention one
             of the user's entities, skip search entirely.
          3. Otherwise, run search (also bounded by the deadline) and
             enqueue the highest-scored hits as a fallback.

        Returns ``(pages, ordered)`` where ``ordered`` is the
        priority-tagged trail of every URL we tried.
        """
        pages: list[FetchedPage] = []
        ordered: list[tuple[int, TrailStep]] = []
        seen_urls: set[str] = set()

        def _track(url: str) -> bool:
            if url in seen_urls:
                return False
            seen_urls.add(url)
            return True

        # ----- Step 1: fetch start URLs first -----
        budget_pages = self.cfg.max_pages
        budget_hops = self.cfg.max_hops
        frontier: list[tuple[int, TrailStep]] = []

        for u in start_urls_list:
            if not _track(u):
                continue
            ordered.append((0, TrailStep(url=u, hop=0,
                                         reason="start-url", score=1.0)))
            result.trail.append(TrailStep(url=u, hop=0,
                                          reason="start-url", score=1.0))
            frontier.append((0, TrailStep(url=u, hop=0,
                                          reason="start-url", score=1.0)))

        # Drain start URLs (and immediate in-page links) within deadline.
        pages, _ = await self._drain_frontier(
            understanding, frontier, pages, seen_urls, ordered,
            budget_pages, budget_hops, deadline, result,
        )

        # ----- Step 2: decide whether we need search fallback -----
        need_search = (
            not start_urls_list
            or not pages
            or (
                not self._any_candidate_relevant(pages, understanding)
                and not deadline.exceeded()
                and len(pages) < budget_pages
            )
        )

        if need_search and not deadline.exceeded():
            try:
                search_hits = await self.search.search_all(
                    understanding.query_variants,
                    limit=self.cfg.search_topk,
                    deadline=deadline,
                )
            except TypeError:
                # Backwards-compat: provider may not accept ``deadline``.
                search_hits = await self.search.search_all(
                    understanding.query_variants,
                    limit=self.cfg.search_topk,
                )
            # Same-domain filter if we had a start domain.
            if start_urls_list:
                search_hits = [
                    h for h in search_hits
                    if same_registrable_domain(h.url, start_urls_list[0])
                ]
            new_frontier: list[tuple[int, TrailStep]] = []
            for hit in search_hits:
                if not _track(hit.url):
                    continue
                ordered.append((
                    1,
                    TrailStep(url=hit.url, hop=0,
                              reason=f"search:{hit.source}",
                              score=hit.score, title=hit.title),
                ))
                result.trail.append(TrailStep(
                    url=hit.url, hop=0,
                    reason=f"search:{hit.source}",
                    score=hit.score, title=hit.title,
                ))
                new_frontier.append((
                    0,
                    TrailStep(url=hit.url, hop=0,
                              reason=f"search:{hit.source}",
                              score=hit.score, title=hit.title),
                ))
            if new_frontier:
                pages, _ = await self._drain_frontier(
                    understanding, new_frontier, pages, seen_urls, ordered,
                    budget_pages, budget_hops, deadline, result,
                )

        return pages, ordered

    async def _drain_frontier(
        self,
        understanding: Any,
        frontier: list[tuple[int, TrailStep]],
        pages: list[FetchedPage],
        seen_urls: set[str],
        ordered: list[tuple[int, TrailStep]],
        budget_pages: int,
        budget_hops: int,
        deadline: Deadline,
        result: AgentResult,
    ) -> tuple[list[FetchedPage], list[tuple[str, str]]]:
        """Walk a frontier until budget, hops, or deadline is reached."""
        more_links: list[tuple[str, str]] = []
        while frontier and len(pages) < budget_pages and not deadline.exceeded():
            hop, step = frontier.pop(0)
            if step.url in {p.url for p in pages} or \
               step.url in {p.final_url for p in pages}:
                continue
            page = await self.fetcher.fetch(step.url, deadline=deadline)
            if page is None or not page.ok:
                result.trail.append(
                    TrailStep(
                        url=step.url, hop=hop,
                        reason=(
                            f"fetch-failed:{step.reason}:"
                            f"{getattr(page, 'error', '') if page else 'none'}"
                        ),
                        score=step.score, title=step.title,
                    )
                )
                continue
            page.hop = hop
            pages.append(page)
            result.pages_fetched += 1
            result.trail.append(
                TrailStep(
                    url=step.url, hop=hop, reason="fetched",
                    score=step.score, title=page.title or step.title,
                )
            )
            flags = self.fetcher.flag_injection(page.html or "")
            if flags:
                result.reasoning += f"; injection_flags={len(flags)}"

            if hop < budget_hops and not deadline.exceeded():
                anchor_texts = self.fetcher.extract_anchor_texts(
                    page.html or "", page.final_url or page.url,
                )
                for href, anchor in anchor_texts:
                    norm = normalize_url(href)
                    if norm in seen_urls:
                        continue
                    if not self._link_relevant(href, anchor, understanding):
                        continue
                    seen_urls.add(norm)
                    frontier.append((
                        hop + 1,
                        TrailStep(url=href, hop=hop + 1,
                                  reason="in-page-link"),
                    ))
                    more_links.append((href, anchor))
        return pages, more_links

    @staticmethod
    def _any_candidate_relevant(
        pages: list[FetchedPage],
        understanding: Any,
    ) -> bool:
        """Cheap heuristic: does any fetched page text mention one of
        the user's entities? Used to decide whether the search fallback
        is needed before spending the budget."""
        if not understanding.entities:
            return True
        for p in pages:
            text = (p.text or "").lower()
            for e in understanding.entities:
                if len(e) >= 3 and e.lower() in text:
                    return True
        return False

    @staticmethod
    def _link_relevant(link: str, anchor: str, understanding: Any) -> bool:
        """Decide whether to follow a discovered link.

        A link is relevant if:
          - any entity token appears in the link URL or anchor text, OR
          - the URL path matches an intent-aware whitelist, OR
          - the anchor text matches an intent-aware whitelist, OR
          - the link is same-domain (always follow at least one step).
        """
        low_link = link.lower()
        low_anchor = (anchor or "").lower()
        # Entity match.
        for ent in understanding.entities:
            if ent.lower() in low_link or ent.lower() in low_anchor:
                return True
        # Focus-token match (non-stopword tokens from query).
        for tok in understanding.focus_tokens:
            if tok in low_link or tok in low_anchor:
                return True
        # Intent-aware path / anchor whitelist.
        intent_paths = {
            "price": ("/price", "/pricing", "/cost", "/buy", "/order", "/shop",
                      "/spec", "/specs", "/specification", "/datasheet", "/product"),
            "contact": ("/contact", "/about", "/support", "/help", "/reach"),
            "hours": ("/hours", "/store-hours", "/opening", "/location", "/locations"),
            "spec": ("/spec", "/specs", "/specification", "/datasheet", "/details"),
            "date": ("/release", "/changelog", "/news", "/blog", "/announcement"),
            "location": ("/location", "/store-locator", "/find", "/stores"),
            "definition": ("/about", "/glossary", "/docs", "/faq"),
        }
        intent_anchors = {
            "price": ("price", "cost", "buy", "pricing", "shop", "specs",
                      "specification", "details", "datasheet"),
            "contact": ("contact", "support", "email", "phone", "reach"),
            "hours": ("hours", "opening", "open", "store hours"),
            "spec": ("spec", "specification", "datasheet", "details"),
            "date": ("release", "changelog", "what's new", "news"),
            "location": ("locations", "store locator", "find us"),
            "definition": ("about", "what is", "glossary"),
        }
        paths = intent_paths.get(understanding.intent, ())
        anchors = intent_anchors.get(understanding.intent, ())
        if any(p in low_link for p in paths):
            return True
        if any(a in low_anchor for a in anchors):
            return True
        return False


async def run_query(
    query: str,
    start_urls: list[str] | None = None,
    cfg: AgentConfig | None = None,
) -> AgentResult:
    """Convenience: run a single query and close the agent."""
    async with Agent(cfg) as agent:
        return await agent.run(query, start_urls=start_urls)


# Allow `python -m app.agent` to run a single query from CLI for testing.
def _cli() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Run a single query.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--start-url", action="append", default=[])
    args = parser.parse_args()

    async def _main() -> None:
        result = await run_query(args.query, start_urls=args.start_url or None)
        print(json.dumps(result.to_contract_json(), indent=2))

    asyncio.run(_main())
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())