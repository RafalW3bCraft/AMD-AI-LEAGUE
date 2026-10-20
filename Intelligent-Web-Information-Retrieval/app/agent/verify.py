"""Stage 5: VERIFY — every candidate MUST be supported by source text.

Rules:
  1. Value must literally appear in the page text it cites.
  2. Token-boundary regex prevents false matches (e.g. `4.3.2` in `2432`).
  3. Independent multi-source agreement boosts confidence.
  4. If no candidate passes: REFUSE (empty answer, no citations).

This stage is the entire point of the agent: a wrong answer is worse
than no answer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

from .config import AgentConfig
from .extract import Candidate
from .fetch import FetchedPage

log = logging.getLogger("match4.verify")


@dataclass
class VerifiedCandidate:
    value: str
    kind: str
    citations: list[str] = field(default_factory=list)
    confidence: float = 0.0
    support_count: int = 0
    agreement: float = 0.0     # 0..1 fraction of distinct sources that agree
    reasoning: str = ""
    # Iteration-5: candidate value + evidence metadata for tie-breaking.
    value_length: int = 0
    evidence_kind: str = ""
    has_currency_marker: bool = False


@dataclass
class VerificationResult:
    best: VerifiedCandidate | None
    verified: list[VerifiedCandidate] = field(default_factory=list)
    refused: bool = True


def verify_candidates(
    candidates: list[Candidate],
    pages: list[FetchedPage],
    intent: str,
    cfg: AgentConfig,
    entities: list[str] | None = None,
) -> VerificationResult:
    """Verify candidates against their source pages.

    When ``entities`` is supplied (non-empty), a candidate is only
    considered supported if its evidence is associated with one of the
    asked entities in the same record/row/block. This prevents the
    common failure mode where a page with two products returns the wrong
    product's price.
    """
    # Index page text by URL for fast lookup.
    page_by_url = {p.final_url or p.url: p for p in pages}
    page_by_url.update({p.url: p for p in pages})

    entities = [e for e in (entities or []) if e]
    # Filter to candidates that pass the entity gate first.
    gated: list[Candidate] = []
    if entities:
        for c in candidates:
            if _candidate_matches_entity(c, page_by_url, entities, intent):
                gated.append(c)
    else:
        gated = list(candidates)

    # Bucket candidates by a normalized value.
    buckets: dict[str, list[Candidate]] = {}
    for c in gated:
        if not c.value:
            continue
        norm = _normalize_value(c.value, intent)
        if not norm:
            continue
        buckets.setdefault(norm, []).append(c)

    verified: list[VerifiedCandidate] = []
    for norm, group in buckets.items():
        support = _collect_support(group, page_by_url)
        if support.support_count < cfg.min_support:
            continue
        verified.append(support)

    # Sort: more support first, then higher confidence, then prefer
    # structured evidence, then currency-marker, then a value-length
    # heuristic that depends on intent:
    #   - for fact/contact/price/spec: longer is usually more specific
    #   - for faq/definition: shorter is usually more direct
    # finally, lower hops.
    def _key(v: VerifiedCandidate) -> tuple:
        if intent in ("faq", "definition", "general", "how_to"):
            length_sign = 1  # prefer shorter
        else:
            length_sign = -1  # prefer longer
        return (
            -v.support_count,
            -v.confidence,
            0 if v.evidence_kind in ("jsonld", "table") else 1,
            0 if v.has_currency_marker else 1,
            length_sign * v.value_length,
            _avg_hop(v),
        )

    verified.sort(key=_key)

    if not verified:
        return VerificationResult(best=None, verified=[], refused=True)

    best = verified[0]
    log.info(
        "verify: best=%r support=%d agreement=%.2f candidates_total=%d",
        best.value[:80], best.support_count, best.agreement, len(candidates),
    )
    return VerificationResult(best=best, verified=verified, refused=False)


def _candidate_matches_entity(
    c: Candidate,
    page_by_url: dict[str, FetchedPage],
    entities: list[str],
    intent: str,
) -> bool:
    """Return True if the candidate's evidence is associated with one of
    the asked entities in the same record/row/block.

    The check is STRICT: when structured evidence (JSON-LD, table row) is
    available, the entity must appear in that LOCAL evidence, not just
    somewhere on the page. This prevents the common failure mode where a
    page with two products returns the WRONG product's price because
    both products' names appear on the same page.

    Special cases:
      - If the candidate has no associated evidence at all (text
        candidate on a page with no entity mentioned anywhere), fall
        back to page-level matching.
      - For JSON-LD and table candidates, the local evidence is
        authoritative: the entity MUST appear in the local evidence to
        pass, regardless of page-level context.
    """
    if not entities:
        return True

    # If the candidate already carries an associated entity string and
    # it matches one of the asked entities, accept.
    if c.entity:
        low_c = c.entity.lower()
        for e in entities:
            if e.lower() == low_c or e.lower() in low_c or low_c in e.lower():
                return True
        # Entity was set but does not match → explicitly wrong entity.
        return False

    # For structured evidence (JSON-LD or table), the LOCAL evidence is
    # authoritative — BUT only for intents where the answer is bound to a
    # specific entity (price, contact, hours, spec, date, location). For
    # faq/definition/general/how_to intents, the answer is page-level
    # information that may apply to the entity in question without
    # literally mentioning it on the same record/row.
    is_structured = c.evidence_kind in ("jsonld", "table")
    if is_structured:
        if intent in ("faq", "definition", "general", "how_to", "list"):
            # Page-level answer; no local-evidence entity requirement.
            return True
        local_evidence: list[str] = []
        if c.evidence_kind == "table" and c.evidence_text:
            local_evidence.append(c.evidence_text.lower())
        if c.evidence_kind == "jsonld" and c.raw_evidence:
            local_evidence.append(c.raw_evidence.lower())
        # Also check evidence_text (which is the rendered evidence for
        # both kinds).
        if c.evidence_text:
            local_evidence.append(c.evidence_text.lower())
        if c.snippet:
            local_evidence.append(c.snippet.lower())
        for evidence in local_evidence:
            for e in entities:
                if len(e) >= 3 and e.lower() in evidence:
                    return True
        # Structured candidate with no entity in local evidence → reject.
        return False

    # Plain-text candidate: when the snippet contains MULTIPLE
    # distinct entities, the asked entity must appear IN the snippet
    # (not just anywhere on the page). This prevents returning the
    # wrong product's value when the page lists several products.
    page = page_by_url.get(c.page_url)
    if page is None:
        return True  # No context to check → accept.
    haystacks = [c.snippet or "", c.value or "", page.text or ""]
    block = " ".join(haystacks).lower()
    if not any(e.lower() in block for e in entities if len(e) >= 3):
        return True
    # The entity appears on the page. If the snippet contains
    # exactly one of the asked entities AND it matches → accept.
    # If the snippet contains multiple distinct asked-entity
    # mentions, we require the snippet to actually include one
    # of them (otherwise it's too ambiguous).
    snippet_low = (c.snippet or "").lower()
    if c.snippet and any(
        e.lower() in snippet_low for e in entities if len(e) >= 3
    ):
        return True
    return False


def _avg_hop(v: VerifiedCandidate) -> float:
    # Hop is on Candidate, not VerifiedCandidate — average across support list
    # would need carrying it through; we approximate via confidence.
    return -v.confidence


# --- helpers --------------------------------------------------------------


def _normalize_value(value: str, intent: str) -> str:
    """Normalize a candidate value so equivalent representations
    bucket together. Conservative: only collapse obvious whitespace and
    currency/case noise."""
    s = value.strip()
    if not s:
        return ""
    # Collapse whitespace.
    s = re.sub(r"\s+", " ", s)
    # Lowercase phone numbers / emails so formats match.
    if intent == "contact":
        if "@" in s:
            return s.lower()
        digits = re.sub(r"\D", "", s)
        return digits or s
    if intent == "price":
        # "$1,299.00" and "USD 1299" should NOT bucket together — keep as-is
        # but normalize whitespace.
        return s
    # For sentences, take the first 200 chars and normalize spaces.
    return s[:200]


def _value_in_text(value: str, text: str) -> bool:
    """Token-boundary substring search.

    Treats common separators (space, -, _, ., /) as optional, so:
        value  "4.3.2"  matches text "...v4.3.2 released..."  ✓
        value  "4.3.2"  matches text "...latency=2432..."     ✗

    Currency normalization: strips leading currency markers ($ € £ USD
    EUR GBP) and removes digit-grouping commas, so:
        value  "$1299"  matches text "Price: $1,299 per unit"  OK

    Strategy:
      1. Normalize value and text: drop currency prefix, drop grouping
         commas, collapse whitespace.
      2. Tokenize value on whitespace; join tokens with [\\s._\\-/]*
         (separator-flexible match).
      3. Anchor with negative lookarounds (?<![A-Za-z0-9]) /
         (?![A-Za-z0-9]) so the match respects token boundaries.
    """
    if not value or not text:
        return False
    v_norm = _normalize_for_match(value)
    t_norm = _normalize_for_match(text)
    if not v_norm:
        return False
    tokens = re.findall(r"\S+", v_norm)
    if not tokens:
        return False
    pattern = r"[\s._\-/]*".join(re.escape(t) for t in tokens)
    # Strict token boundaries on both sides (negative lookarounds).
    # This prevents `4.3.2` from matching inside `2432` while still
    # matching plain occurrences like "version 4.3.2 released".
    pattern = rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])"
    try:
        return re.search(pattern, t_norm, flags=re.I) is not None
    except re.error:
        return v_norm.lower() in t_norm.lower()


_CURRENCY_PREFIX_RE = re.compile(r"^\s*(?:US\$|USD|EUR|GBP|\$|€|£|Rs\.?|INR|JPY|¥)\s*")
_DIGIT_GROUP_COMMA_RE = re.compile(r"(\d),(\d)")
_WS_NORM_RE = re.compile(r"\s+")
# Strip currency markers + grouping commas; collapse whitespace.
def _normalize_for_match(s: str) -> str:
    s = _CURRENCY_PREFIX_RE.sub("", s)
    # Only strip commas that sit between digits.
    while True:
        new = _DIGIT_GROUP_COMMA_RE.sub(r"\1\2", s)
        if new == s:
            break
        s = new
    s = _WS_NORM_RE.sub(" ", s).strip()
    return s


def _collect_support(
    group: list[Candidate],
    page_by_url: dict[str, FetchedPage],
) -> VerifiedCandidate:
    """Verify a bucket of same-valued candidates against their pages.

    A candidate is "supported" if its value can be found (token-boundary
    regex) in:
      - the page's rendered text (after script/style stripping), OR
      - the page's raw HTML/JSON-LD evidence when the candidate was
        structured (so JSON-LD-only values can be verified), OR
      - the page's pre-extracted structured evidence stored on the
        Candidate itself (``evidence_text``).
    """
    primary = group[0]
    citations: list[str] = []
    support_count = 0
    structured_hits = 0
    agreement_pages: set[str] = set()
    evidence_audit: list[str] = []

    for c in group:
        page = page_by_url.get(c.page_url)
        if page is None:
            continue
        # Layer 1: page.text (rendered text).
        in_text = _value_in_text(c.value, page.text or "")
        # Layer 2: page.html (raw HTML — picks up JSON-LD scripts).
        in_html = _value_in_text(c.value, page.html or "")
        # Layer 3: candidate's own evidence_text (JSON-LD blob rendering).
        in_evidence = bool(c.evidence_text) and _value_in_text(c.value, c.evidence_text)
        if not (in_text or in_html or in_evidence):
            continue
        support_count += 1
        if c.page_url not in citations:
            citations.append(c.page_url)
        if c.structured:
            structured_hits += 1
        agreement_pages.add(_page_key(page))
        # Capture audit trail of which evidence layer proved it.
        layer = "text" if in_text else ("html" if in_html else "evidence_text")
        evidence_audit.append(
            f"{c.page_url}#{c.evidence_path} via {layer}"
        )

    if support_count == 0:
        return VerifiedCandidate(
            value=primary.value,
            kind=primary.kind,
            citations=[],
            confidence=0.0,
            support_count=0,
            agreement=0.0,
            reasoning="no source supports this value",
        )

    # Confidence model.
    base = 0.4
    base += 0.25 if primary.structured else 0.0
    base += 0.05 * min(support_count - 1, 4)        # up to +0.20 for multi-source
    base += 0.15 * (structured_hits / max(1, support_count))
    # Lower-hop bonus.
    base += 0.1 if primary.hop == 0 else 0.0
    confidence = min(0.99, base)

    agreement = len(agreement_pages) / max(1, support_count)

    return VerifiedCandidate(
        value=primary.value,
        kind=primary.kind,
        citations=citations,
        confidence=round(confidence, 3),
        support_count=support_count,
        agreement=round(agreement, 3),
        reasoning=(
            f"value supported by {support_count} occurrence(s) "
            f"across {len(agreement_pages)} page(s); "
            f"structured={primary.structured}; evidence={'; '.join(evidence_audit[:3])}"
        ),
        value_length=len(primary.value),
        evidence_kind=primary.evidence_kind or "text",
        has_currency_marker=any(c in primary.value for c in ("$", "€", "£", "¥", "USD", "EUR")),
    )


def _page_key(page: FetchedPage) -> str:
    """Group pages by domain+path-stem for agreement counting."""
    from urllib.parse import urlparse
    p = urlparse(page.final_url or page.url)
    path = re.sub(r"\d+", "N", p.path or "")
    return f"{p.netloc}{path}"