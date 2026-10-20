"""Tests for Stage 4 EXTRACT and Stage 5 VERIFY.

These tests use a tiny in-memory corpus of FetchedPage objects so they
are deterministic and do not require network access.
"""

from app.agent.config import AgentConfig
from app.agent.extract import Candidate, extract_candidates
from app.agent.fetch import FetchedPage
from app.agent.verify import _value_in_text, verify_candidates


# ---------- extract -------------------------------------------------------

HTML_PRODUCT = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "TQ-40 Processor",
  "description": "A 40-TOPS edge AI accelerator",
  "offers": {
    "@type": "Offer",
    "price": "1299",
    "priceCurrency": "USD"
  }
}
</script>
</head><body>
<p>The TQ-40 is $1,299.00 per unit at 1k volume.</p>
<p>Specifications: 40 TOPS, 28W TDP, weight 45g.</p>
<p>Hours: Mon-Fri 9:00 AM - 6:00 PM</p>
<p>Phone: +1 (555) 123-4567</p>
<p>Email: sales@example.com</p>
<p>Released: 2026-09-15</p>
</body></html>
"""


def _make_page(url: str, html: str, hop: int = 0) -> FetchedPage:
    from app.agent.fetch import Fetcher
    text = Fetcher._html_to_text(html)
    title = Fetcher._extract_title(html)
    return FetchedPage(
        url=url, final_url=url, status=200,
        title=title, text=text, html=html, hop=hop, ok=True,
    )


def test_extract_jsonld_price():
    page = _make_page("https://example.com/tq-40", HTML_PRODUCT)
    cands = extract_candidates(
        pages=[page], intent="price", entities=["TQ-40"], raw_query="price",
        cfg=AgentConfig(),
    )
    assert any("1299" in c.value for c in cands), \
        f"got: {[(c.kind, c.value) for c in cands]}"


def test_extract_text_price():
    page = _make_page("https://example.com/tq-40", HTML_PRODUCT)
    cands = extract_candidates(
        pages=[page], intent="price", entities=["TQ-40"], raw_query="",
        cfg=AgentConfig(),
    )
    assert any("$1,299" in c.value for c in cands)


def test_extract_text_phone():
    page = _make_page("https://example.com/contact", HTML_PRODUCT)
    cands = extract_candidates(
        pages=[page], intent="contact", entities=[], raw_query="",
        cfg=AgentConfig(),
    )
    assert any("555" in c.value and "123" in c.value and "4567" in c.value
               for c in cands)


def test_extract_text_email():
    page = _make_page("https://example.com/contact", HTML_PRODUCT)
    cands = extract_candidates(
        pages=[page], intent="contact", entities=[], raw_query="",
        cfg=AgentConfig(),
    )
    assert any("sales@example.com" in c.value for c in cands)


def test_extract_text_hours():
    page = _make_page("https://example.com/hours", HTML_PRODUCT)
    cands = extract_candidates(
        pages=[page], intent="hours", entities=[], raw_query="",
        cfg=AgentConfig(),
    )
    assert any("Mon" in c.value for c in cands)


def test_extract_text_date():
    page = _make_page("https://example.com/release", HTML_PRODUCT)
    cands = extract_candidates(
        pages=[page], intent="date", entities=[], raw_query="",
        cfg=AgentConfig(),
    )
    assert any("2026" in c.value for c in cands)


# ---------- verify -------------------------------------------------------

def test_verify_token_boundary_prevents_false_match():
    """`4.3.2` must NOT match text `2432` or `24321`."""
    assert not _value_in_text("4.3.2", "latency_ms=2432")
    assert not _value_in_text("4.3.2", "build 24321 released")
    # And it must NOT match `4.32.1` (different version) — strict token
    # boundaries mean `4` of `4.32.1` is not adjacent to `.3.2`.
    assert not _value_in_text("4.3.2", "release 4.32.1 shipped")


def test_verify_value_matches_real_occurrence():
    # Plain-occurrence match (with whitespace around the version).
    assert _value_in_text("4.3.2", "firmware version 4.3.2 was released")
    # Leading "v" should also match — typical version annotation.
    # `v` is a single non-alnum word, then `4.3.2` follows.
    # We test the strict boundary case here; the `v` prefix is handled
    # by the call site (extract.py emits "v4.3.2" when present in text).
    assert _value_in_text("v4.3.2", "firmware version v4.3.2 was released")
    # And `4.3.2` must NOT match `v4.3.2` if we use strict boundaries
    # (the call site is responsible for the prefix).
    # This documents current behavior:
    assert not _value_in_text("4.3.2", "firmware version v4.3.2 was released")


def test_verify_token_boundary_with_currency():
    assert _value_in_text("$1299", "Price: $1,299 per unit")
    assert not _value_in_text("$1299", "Price: $12,991 per unit")


def test_verify_candidate_needs_source_support():
    page = _make_page("https://example.com/x", HTML_PRODUCT)
    cands = extract_candidates(
        pages=[page], intent="price", entities=[], raw_query="",
        cfg=AgentConfig(),
    )
    result = verify_candidates(cands, [page], "price", AgentConfig())
    assert result.best is not None
    assert result.best.support_count >= 1
    assert result.best.value


def test_verify_refuses_unsupported_value():
    page = _make_page("https://example.com/x",
                      "<html><body>nothing here</body></html>")
    cands = [
        Candidate(value="FAKE", kind="text:price", page_url=page.url, snippet=""),
    ]
    result = verify_candidates(cands, [page], "price", AgentConfig())
    assert result.best is None


def test_verify_multi_source_agreement():
    page_a = _make_page("https://a.example.com/x", HTML_PRODUCT)
    page_b = _make_page("https://b.example.com/x", HTML_PRODUCT)
    cands = extract_candidates(
        pages=[page_a, page_b], intent="price", entities=[], raw_query="",
        cfg=AgentConfig(),
    )
    result = verify_candidates(cands, [page_a, page_b], "price", AgentConfig())
    assert result.best is not None
    assert result.best.confidence > 0.5