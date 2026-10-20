"""Held-out benchmark suite for retrieval accuracy (Iteration 2).

This is the "truth set" required by the iteration 2 exit gate: a list of
questions with expected values, expected URLs, expected evidence types,
and target answer types. Each benchmark case is independent — its
expected values are NOT referenced from agent logic, only from this
test file (held-out benchmark).

Run with::

    pytest tests/test_iter2_benchmark.py -v

Or run with the report generator::

    python -m reports.run_benchmark > reports/benchmark.md
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from app.agent import Agent
from app.agent.config import AgentConfig
from app.agent.search import Hit, StubProvider


# ----------------------------- truth set --------------------------------

@dataclass
class TruthCase:
    """One truth-set example."""
    name: str
    query: str
    expected_value_substr: str     # substring of the expected answer
    expected_value_not_substr: str = ""  # substring that must NOT appear
    expected_url_substr: str = ""
    expected_evidence_kind: str = ""      # "jsonld" | "table" | "text" | ""
    target_answer_type: str = ""          # "exact" | "refusal" | "partial"
    description: str = ""


TRUTH_CASES: list[TruthCase] = [
    TruthCase(
        name="two-products-tq60",
        query="How much does the TQ-60 Processor cost?",
        expected_value_substr="2499",
        expected_value_not_substr="1299",
        expected_url_substr="/catalog",
        expected_evidence_kind="table",
        target_answer_type="exact",
        description="Page with two products; agent must pick the asked one.",
    ),
    TruthCase(
        name="two-products-tq40",
        query="price of TQ-40",
        expected_value_substr="1299",
        expected_value_not_substr="2499",
        expected_url_substr="/catalog",
        expected_evidence_kind="table",
        target_answer_type="exact",
        description="Same page as above; agent must pick the OTHER product.",
    ),
    TruthCase(
        name="jsonld-graph-tq40",
        query="price of TQ-40 Processor",
        expected_value_substr="1299",
        expected_url_substr="/catalog-jsonld",
        expected_evidence_kind="jsonld",
        target_answer_type="exact",
        description="JSON-LD @graph with two products; must pick asked one.",
    ),
    TruthCase(
        name="jsonld-graph-tq60",
        query="TQ-60 Processor price",
        expected_value_substr="2499",
        expected_value_not_substr="1299",
        expected_url_substr="/catalog-jsonld",
        expected_evidence_kind="jsonld",
        target_answer_type="exact",
        description="JSON-LD @graph with two products; reverse case.",
    ),
    TruthCase(
        name="navigation-finds-specs",
        query="TQ-40 price",
        expected_value_substr="1299",
        expected_url_substr="/tq40",
        target_answer_type="exact",
        description="Start on product page; specs link must be followed.",
    ),
    TruthCase(
        name="contact-from-organization",
        query="What's the phone number for Acme?",
        expected_value_substr="555",
        expected_url_substr="/contact",
        target_answer_type="exact",
        description="Organization JSON-LD with contactPoint → phone.",
    ),
    TruthCase(
        name="faq-acceptedAnswer",
        query="What is the warranty period for TQ-40?",
        expected_value_substr="2 years",
        expected_url_substr="/faq",
        target_answer_type="exact",
        description="FAQPage with acceptedAnswer.text → answer text.",
    ),
    TruthCase(
        name="redirect-resolves",
        query="TQ-40 price",
        expected_value_substr="1299",
        target_answer_type="exact",
        description="302 redirect followed; answer still grounded.",
    ),
    TruthCase(
        name="search-empty-start-url-works",
        query="TQ-40 price",
        expected_value_substr="1299",
        expected_url_substr="/tq40",
        target_answer_type="exact",
        description="Empty search; start URL is the only evidence source.",
    ),
    TruthCase(
        name="off-site-distraction-filtered",
        query="TQ-40 price",
        expected_value_substr="1299",
        target_answer_type="exact",
        description="Off-site distraction hits must not displace start URL.",
    ),
    TruthCase(
        name="no-evidence-refuses",
        query="What is the meaning of life?",
        expected_value_substr="",
        target_answer_type="refusal",
        description="No evidence on page → empty answer, no citation.",
    ),
]


# ----------------------------- server ------------------------------------

BENCHMARK_HTML = {
    "/catalog": """<html><body>
<table>
  <tr><th>Product</th><th>Price</th><th>SKU</th></tr>
  <tr><td>TQ-40 Processor</td><td>$1,299</td><td>TQ40</td></tr>
  <tr><td>TQ-60 Processor</td><td>$2,499</td><td>TQ60</td></tr>
</table>
</body></html>""",
    "/catalog-jsonld": """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org",
 "@graph":[
  {"@type":"Product","name":"TQ-40 Processor","sku":"TQ40",
   "offers":{"@type":"Offer","price":"1299","priceCurrency":"USD"}},
  {"@type":"Product","name":"TQ-60 Processor","sku":"TQ60",
   "offers":{"@type":"Offer","price":"2499","priceCurrency":"USD"}}
 ]}
</script></head><body></body></html>""",
    "/tq40": """<html><head>
<script type="application/ld+json">
{"@type":"Product","name":"TQ-40 Processor",
 "offers":{"@type":"Offer","price":"1299","priceCurrency":"USD"}}
</script></head><body>
<a href="/specs">Full specs</a>
<a href="/contact">Contact us</a>
</body></html>""",
    "/specs": """<html><head><title>TQ-40 Specifications</title></head>
<body><p>TQ-40 lists at $1,299 USD.</p></body></html>""",
    "/contact": """<html><head>
<script type="application/ld+json">
{"@type":"Organization","name":"Acme",
 "contactPoint":{"@type":"ContactPoint",
   "telephone":"+1-555-123-4567","email":"sales@acme.example"}}
</script></head><body>
<p>Call +1-555-123-4567.</p>
</body></html>""",
    "/faq": """<html><head>
<script type="application/ld+json">
{"@type":"FAQPage","mainEntity":[
  {"@type":"Question","name":"What is the warranty period?",
   "acceptedAnswer":{"@type":"Answer","text":"2 years from purchase."}}
 ]}
</script></head><body><p>Warranty: 2 years.</p></body></html>""",
    "/blog": """<html><body><p>Random content. TQ-40 price is $99 (wrong).</p>
<a href="https://other.example.org/">off-site</a>
<a href="/tq40">real page</a></body></html>""",
}


@pytest.fixture
async def bench_server():
    app = web.Application()
    for path, body in BENCHMARK_HTML.items():
        async def handler(request, body=body):
            return web.Response(text=body, content_type="text/html")
        app.router.add_get(path, handler)
    async def redir(request):
        return web.Response(status=302, headers={"Location": "/tq40"})
    app.router.add_get("/redirect-tq40", redir)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    try:
        yield base
    finally:
        await runner.cleanup()


def _make_agent(bench_server: str, *, stub_hits: list[Hit] | None = None) -> Agent:
    cfg = AgentConfig(
        search_provider="stub",
        stub_corpus=stub_hits or [],
        max_pages=4,
        max_hops=2,
        fetch_timeout_s=4.0,
        query_deadline_s=10.0,
        output_reserve_s=0.5,
        allow_local_network=True,
    )
    agent = Agent(cfg)
    agent.search = StubProvider(corpus=stub_hits or [])  # type: ignore[assignment]
    return agent


# ------------------------- case-to-args map ------------------------------

def _case_args(case: TruthCase, bench_server: str) -> dict[str, Any]:
    """Map a truth case to (start_urls, stub_hits, expected_url_substr)."""
    starts: list[str] = []
    stubs: list[Hit] = []
    if case.name == "two-products-tq60":
        starts = [f"{bench_server}/catalog"]
    elif case.name == "two-products-tq40":
        starts = [f"{bench_server}/catalog"]
    elif case.name == "jsonld-graph-tq40":
        starts = [f"{bench_server}/catalog-jsonld"]
    elif case.name == "jsonld-graph-tq60":
        starts = [f"{bench_server}/catalog-jsonld"]
    elif case.name == "navigation-finds-specs":
        starts = [f"{bench_server}/tq40"]
    elif case.name == "contact-from-organization":
        starts = [f"{bench_server}/contact"]
    elif case.name == "faq-acceptedAnswer":
        starts = [f"{bench_server}/faq"]
    elif case.name == "redirect-resolves":
        starts = [f"{bench_server}/redirect-tq40"]
    elif case.name == "search-empty-start-url-works":
        starts = [f"{bench_server}/tq40"]
        stubs = []
    elif case.name == "off-site-distraction-filtered":
        starts = [f"{bench_server}/tq40"]
        stubs = [
            Hit(url=f"{bench_server}/blog", title="Random blog",
                snippet="x", source="stub"),
            Hit(url="https://other.example.org/x", title="Off-site",
                snippet="y", source="stub"),
        ]
    elif case.name == "no-evidence-refuses":
        starts = [f"{bench_server}/blog"]
    return {"start_urls": starts, "stub_hits": stubs}


# ----------------------------- tests -------------------------------------

@pytest.mark.parametrize("case", TRUTH_CASES,
                         ids=[c.name for c in TRUTH_CASES])
@pytest.mark.asyncio
async def test_truth_case(bench_server, case: TruthCase):
    args = _case_args(case, bench_server)
    agent = _make_agent(bench_server, stub_hits=args["stub_hits"])
    try:
        result = await agent.run(case.query,
                                 start_urls=args["start_urls"] or None)
    finally:
        await agent.aclose()

    # Verify against truth set.
    if case.target_answer_type == "refusal":
        assert result.answer == "", \
            f"expected refusal but got {result.answer!r}"
        assert result.citations == [], \
            f"expected no citations on refusal; got {result.citations!r}"
        return

    if case.target_answer_type == "exact":
        norm = result.answer.replace(",", "").replace("$", "")
        expected_norm = case.expected_value_substr.replace(",", "").replace("$", "")
        assert expected_norm in norm, \
            f"case {case.name}: expected {case.expected_value_substr!r} in answer; got {result.answer!r}"
        if case.expected_value_not_substr:
            not_norm = case.expected_value_not_substr.replace(",", "").replace("$", "")
            assert not_norm not in norm, \
                f"case {case.name}: should NOT contain {case.expected_value_not_substr!r}; got {result.answer!r}"
        if case.expected_url_substr:
            assert any(case.expected_url_substr in c for c in result.citations), \
                f"case {case.name}: expected URL containing {case.expected_url_substr!r}; got {result.citations!r}"
        assert result.confidence > 0.0, \
            f"case {case.name}: expected positive confidence; got {result.confidence}"


# -------------------------- benchmark report ----------------------------
# Report generation is handled in tests/conftest.py so pytest can call it
# from its built-in hook system.