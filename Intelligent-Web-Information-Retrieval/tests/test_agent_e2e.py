"""End-to-end test for the orchestrator using the offline StubProvider.

This proves the 5-stage pipeline produces a verified, cited answer
WITHOUT touching the network.
"""

import asyncio

import pytest

from app.agent import Agent
from app.agent.config import AgentConfig
from app.agent.search import Hit


STUB_HITS = [
    Hit(
        url="https://example.com/tq40",
        title="TQ-40 Product Page",
        snippet="The TQ-40 edge AI accelerator costs $1,299.",
        source="stub",
    ),
    Hit(
        url="https://example.com/tq40-specs",
        title="TQ-40 Specifications",
        snippet="Specs: 40 TOPS, 28W TDP, 45g weight.",
        source="stub",
    ),
]


PRODUCT_HTML = """
<html><head><title>TQ-40 Product Page</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"TQ-40",
 "offers":{"@type":"Offer","price":"1299","priceCurrency":"USD"}}
</script></head>
<body>
<p>Welcome to the TQ-40 product page.</p>
<p>Price: $1,299.00 per unit.</p>
<p>Specifications: 40 TOPS, 28W TDP, weight 45g.</p>
<a href="/tq40-specs">Full specs</a>
</body></html>
"""

SPECS_HTML = """
<html><head><title>TQ-40 Specifications</title></head>
<body>
<h1>TQ-40 Specifications</h1>
<p>The TQ-40 is priced at $1,299.</p>
<p>Specs: 40 TOPS, 28W TDP, weight 45g.</p>
</body></html>
"""


def _fake_fetch_factory():
    from app.agent.fetch import Fetcher, FetchedPage

    async def fake_fetch(url: str, deadline=None):
        if "specs" in url:
            html = SPECS_HTML
        else:
            html = PRODUCT_HTML
        return FetchedPage(
            url=url, final_url=url, status=200,
            title=Fetcher._extract_title(html),
            text=Fetcher._html_to_text(html),
            html=html, hop=0, ok=True,
        )
    return fake_fetch


@pytest.mark.asyncio
async def test_e2e_price_query():
    cfg = AgentConfig(search_provider="stub", stub_corpus=STUB_HITS)
    agent = Agent(cfg)
    agent.fetcher.fetch = _fake_fetch_factory()  # type: ignore

    result = await agent.run("How much does the TQ-40 cost?", start_urls=None)
    await agent.aclose()

    assert result.intent == "price"
    assert result.pages_fetched >= 1
    assert result.answer, "agent must produce a non-empty answer for a known question"
    assert result.citations, "agent must cite at least one URL"
    assert "1299" in result.answer, \
        f"answer should contain price; got: {result.answer!r}"
    assert result.confidence > 0.0


@pytest.mark.asyncio
async def test_e2e_refuses_unknown():
    cfg = AgentConfig(search_provider="stub", stub_corpus=STUB_HITS)
    agent = Agent(cfg)

    async def fake_fetch(url: str):
        return None
    agent.fetcher.fetch = fake_fetch  # type: ignore

    result = await agent.run("What is the meaning of life, the universe, and everything?")
    await agent.aclose()

    # No pages → no evidence → refusal.
    assert result.answer == ""
    assert result.citations == []
    assert result.confidence == 0.0