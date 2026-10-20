"""Stage 4: EXTRACT — structure-aware, intent-specific candidate extraction.

Reads JSON-LD, Microdata, HTML tables, and intent-specific patterns from
each FetchedPage, then ranks candidates by:

    1. Structured-source boost (JSON-LD > Microdata > HTML table > text span).
    2. Page authority (lower hop = higher boost).
    3. Entity match (extracted value contains a query entity).
    4. Proximity to entities within the page.

Returns an unsorted list of Candidate objects for the Verify stage to
filter and rank.

Each Candidate also carries ``evidence_kind`` (jsonld|table|text),
``evidence_path`` (a JSON-pointer-style path or HTML row/column locator),
and ``evidence_text`` (a deterministic textual rendering of the source
evidence — the exact bytes/quoted text the candidate was lifted from).
These are what Stage 5 verifies against, so a JSON-LD-only value still
has auditable provenance.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .config import AgentConfig
from .fetch import FetchedPage

log = logging.getLogger("match4.extract")


@dataclass
class Candidate:
    value: str
    kind: str            # "jsonld:Product.price" etc.
    page_url: str        # page where it was found
    page_title: str = ""
    snippet: str = ""    # surrounding text for verify step
    structured: bool = False
    hop: int = 0
    boost: float = 1.0
    # New: provenance for verification.
    evidence_kind: str = "text"            # jsonld | table | text
    evidence_path: str = ""               # "/offers/price" or "row[2].col[1]"
    evidence_text: str = ""               # deterministic rendering of evidence
    raw_evidence: str = ""                # raw JSON for the matched field
    entity: str = ""                      # the entity string this value was associated with

    def __post_init__(self) -> None:
        # Defensive trim.
        self.value = self.value.strip()
        if len(self.value) > 500:
            self.value = self.value[:500]
        if self.evidence_text and len(self.evidence_text) > 800:
            self.evidence_text = self.evidence_text[:800]


# --- Intent-specific regexes ---------------------------------------------

_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PRICE_RE = re.compile(
    r"(?:US\$|USD|\$|€|EUR|£|GBP|Rs\.?|INR|JPY|¥)\s?"
    r"\d{1,3}(?:[\.,]\d{3})*(?:[\.,]\d{1,2})?"
    r"(?:\s*(?:/|per)\s?\w+)?"
    r"|"
    # Bare continental-format number ("1.299,00" or "1,299.99") with
    # an explicit currency marker on the NEXT non-whitespace token.
    r"\d{1,3}(?:[.,]\d{3})*[.,]\d{2}\s+(?:USD|EUR|GBP|JPY|Rs\.?|INR)"
)
_HOURS_RE = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*"
    r"(?:\s*[–-]\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*)?"
    r"[:\s,]*\d{1,2}(?::\d{2})?\s*(?:[Aa][Mm]|[Pp][Mm])?"
    r"\s*(?:[–-]\s*\d{1,2}(?::\d{2})?\s*(?:[Aa][Mm]|[Pp][Mm])?)?"
)
_DATE_RE = re.compile(
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|"
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s*\d{4}\b|"
    r"\b\d{4}-\d{2}-\d{2}\b"
)

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I,
)


def extract_candidates(
    pages: list[FetchedPage],
    intent: str,
    entities: list[str],
    raw_query: str,
    cfg: AgentConfig,
) -> list[Candidate]:
    out: list[Candidate] = []
    for page in pages:
        out.extend(_extract_from_jsonld(page, intent, entities))
        out.extend(_extract_from_tables(page, intent, entities))
        out.extend(_extract_from_text(page, intent, entities, raw_query))
    log.info("extract: %d candidates across %d pages", len(out), len(pages))
    return out


# --- JSON-LD ------------------------------------------------------------

def _extract_from_jsonld(
    page: FetchedPage, intent: str, entities: list[str]
) -> list[Candidate]:
    out: list[Candidate] = []
    for m in _JSONLD_RE.finditer(page.html or ""):
        blob = m.group(1).strip()
        # Strip HTML comments inside JSON-LD (common bug).
        blob = re.sub(r"<!--.*?-->", "", blob, flags=re.S)
        try:
            data = json.loads(blob)
        except Exception:
            # Salvage attempt: @graph wrapper or array wrapper around a dict.
            data = None
            try:
                wrapped = json.loads(blob)
                if isinstance(wrapped, dict) and "@graph" in wrapped:
                    data = wrapped["@graph"]
                elif isinstance(wrapped, list) and wrapped:
                    data = wrapped
            except Exception:
                continue
        if data is None:
            continue
        for node in _iter_nodes(data):
            types = _normalize_type(node.get("@type"))
            if not types:
                continue
            type_label = ",".join(sorted(set(types)))
            value, path = _jsonld_value_for_intent(node, intent, types)
            if value:
                # Keep the FULL node (not truncated) so the verifier can
                # perform entity-match against the actual JSON-LD
                # content. The snippet field is a display-friendly
                # truncated form.
                full_node_text = json.dumps(node, ensure_ascii=False,
                                            separators=(",", ":"))
                snippet = full_node_text[:400]
                evidence_text = _render_evidence_text(node, path, value)
                out.append(
                    Candidate(
                        value=value,
                        kind=f"jsonld:{type_label}.{intent}",
                        page_url=page.final_url or page.url,
                        page_title=page.title,
                        snippet=snippet,
                        structured=True,
                        hop=page.hop,
                        boost=2.0,
                        evidence_kind="jsonld",
                        evidence_path=path,
                        evidence_text=evidence_text,
                        raw_evidence=full_node_text,
                    )
                )
    return out


def _normalize_type(raw: Any) -> list[str]:
    """Return a list of lower-cased @type strings, robust to str | list |
    missing | None | non-string types. Always returns a list (possibly
    empty)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.lower()] if raw else []
    if isinstance(raw, list):
        out: list[str] = []
        for x in raw:
            if isinstance(x, str) and x:
                out.append(x.lower())
            elif isinstance(x, dict) and "@type" in x:
                # Some pages embed {"@type":"Product"} as a single-item dict.
                inner = x.get("@type")
                if isinstance(inner, str):
                    out.append(inner.lower())
                elif isinstance(inner, list):
                    for y in inner:
                        if isinstance(y, str):
                            out.append(y.lower())
        return out
    return []


def _iter_nodes(data):
    """Walk a JSON-LD graph and yield every dict node, including nested
    FAQPage.mainEntity[*].acceptedAnswer and Offer children."""
    if isinstance(data, list):
        for x in data:
            yield from _iter_nodes(x)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data:
            yield from _iter_nodes(data["@graph"])
        if "mainEntity" in data:
            yield from _iter_nodes(data["mainEntity"])
        if "acceptedAnswer" in data:
            yield from _iter_nodes(data["acceptedAnswer"])
        if "answer" in data:
            yield from _iter_nodes(data["answer"])
        if "hasPart" in data:
            yield from _iter_nodes(data["hasPart"])


def _jsonld_value_for_intent(
    node: dict, intent: str, types: list[str]
) -> tuple[str | None, str]:
    """Pick the value (and its JSON-pointer-style path) from a JSON-LD
    node that best matches the intent. Returns ``(value, path)``.

    Supports nested offers (dict or list), priceRange, multiple offers,
    and Organization/LocalBusiness/Event/FAQPage for their respective
    intents. Path is always rooted at ``/`` (the node root).
    """
    if not isinstance(node, dict):
        return None, ""

    # Offers / price for price intent.
    if intent == "price":
        # Walk through offers (dict OR list of dicts).
        offers = node.get("offers")
        offer_paths: list[tuple[Any, str]] = []
        if isinstance(offers, dict):
            offer_paths.append((offers, "/offers"))
        elif isinstance(offers, list):
            for i, o in enumerate(offers):
                if isinstance(o, dict):
                    offer_paths.append((o, f"/offers/{i}"))

        # Also support AggregateOffer fields at the node root.
        agg_keys = ("lowPrice", "highPrice", "price")
        for k in agg_keys:
            v = node.get(k)
            if v is not None and not isinstance(v, (dict, list)):
                path = f"/{k}"
                formatted = _format_price(v, node.get("priceCurrency"))
                if formatted:
                    return formatted, path

        for offer, base_path in offer_paths:
            # Handle nested Offer ({"@type":"Offer","price":...}).
            p = offer.get("price") or offer.get("lowPrice") or offer.get("highPrice")
            if p is not None:
                cur = offer.get("priceCurrency") or node.get("priceCurrency") or ""
                path = f"{base_path}/price"
                formatted = _format_price(p, cur)
                if formatted:
                    return formatted, path
            # Sometimes price lives at /offers/priceSpecification/price.
            spec = offer.get("priceSpecification")
            if isinstance(spec, dict):
                p2 = spec.get("price")
                if p2 is not None:
                    cur = spec.get("priceCurrency") or offer.get("priceCurrency") or ""
                    path = f"{base_path}/priceSpecification/price"
                    formatted = _format_price(p2, cur)
                    if formatted:
                        return formatted, path

        # priceRange fallback.
        if "priceRange" in node:
            return str(node["priceRange"]), "/priceRange"
        if "price" in node and not isinstance(node["price"], (dict, list)):
            v = node["price"]
            return _format_price(v, node.get("priceCurrency")), "/price"

    # Organization / LocalBusiness / contact intent.
    if intent == "contact":
        # contactPoint could be dict or list.
        cps = node.get("contactPoint")
        cps_list: list[tuple[Any, str]] = []
        if isinstance(cps, dict):
            cps_list.append((cps, "/contactPoint"))
        elif isinstance(cps, list):
            for i, c in enumerate(cps):
                if isinstance(c, dict):
                    cps_list.append((c, f"/contactPoint/{i}"))

        for cp, base in cps_list:
            tel = cp.get("telephone") or cp.get("faxNumber")
            if tel:
                return str(tel), f"{base}/telephone"
            email = cp.get("email")
            if email:
                return str(email), f"{base}/email"

        tel = node.get("telephone")
        if tel:
            return str(tel), "/telephone"
        email = node.get("email")
        if email:
            return str(email), "/email"
        addr = node.get("address")
        if isinstance(addr, dict):
            parts = [addr.get(k, "") for k in (
                "streetAddress", "addressLocality", "addressRegion",
                "postalCode", "addressCountry",
            )]
            parts = [p for p in parts if p]
            if parts:
                return ", ".join(str(p) for p in parts), "/address"
        elif isinstance(addr, str):
            return addr, "/address"

    # OpeningHoursSpecification for hours intent.
    if intent == "hours":
        oh = node.get("openingHoursSpecification") or node.get("openingHours")
        if isinstance(oh, list) and oh:
            first = oh[0]
            if isinstance(first, dict):
                days = first.get("dayOfWeek")
                opens = first.get("opens")
                closes = first.get("closes")
                if days and (opens or closes):
                    days_str = ", ".join(days) if isinstance(days, list) else str(days)
                    val = (f"{days_str}: {opens}–{closes}" if opens and closes
                           else f"{days_str}: {opens or closes}")
                    return val, "/openingHoursSpecification/0"
            elif isinstance(first, str):
                return first, "/openingHours/0"
        elif isinstance(oh, str):
            return oh, "/openingHours"

    # Event / dates.
    if intent == "date":
        for k in ("startDate", "endDate", "datePublished", "dateModified"):
            v = node.get(k)
            if v:
                return str(v), f"/{k}"

    # FAQPage — surface the acceptedAnswer text.
    if intent in ("faq", "general", "definition"):
        if "acceptedAnswer" in node:
            aa = node["acceptedAnswer"]
            if isinstance(aa, dict) and "text" in aa:
                return str(aa["text"])[:300], "/acceptedAnswer/text"
            if isinstance(aa, str):
                return aa[:300], "/acceptedAnswer"
        if "answer" in node:
            return str(node["answer"])[:300], "/answer"

    # Definition: name + description.
    if intent == "definition":
        if "name" in node and "description" in node:
            val = f"{node['name']}: {node['description']}"[:300]
            return val, "/name+description"

    # Generic fallback — return a one-line summary if no intent match.
    if intent == "general":
        if "name" in node and "description" in node:
            val = f"{node['name']}: {node['description']}"[:300]
            return val, "/name+description"

    return None, ""


def _format_price(value: Any, currency: Any) -> str | None:
    """Render a JSON-LD price as ``"<currency> <value>"`` if currency given,
    otherwise just the value. Falls back to ``str(value)`` when needed."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    cur = ""
    if isinstance(currency, str):
        cur = currency.strip()
    elif isinstance(currency, list) and currency and isinstance(currency[0], str):
        cur = currency[0].strip()
    if cur and not text.startswith(("$", "€", "£", "US$")):
        return f"{cur} {text}"
    return text


def _render_evidence_text(node: dict, path: str, value: str) -> str:
    """Build a deterministic text rendering of the matched evidence so the
    verify stage can perform token-boundary matching against it even when
    the value only exists in the JSON-LD blob."""
    try:
        rendered = json.dumps(node, ensure_ascii=False, indent=2)
    except Exception:
        rendered = repr(node)
    return f"jsonld_path={path} value={value!r} node={rendered}"


# --- HTML tables ---------------------------------------------------------

_TABLE_RE = re.compile(r"<table[^>]*>.*?</table>", re.S | re.I)
_TR_RE = re.compile(r"<tr[^>]*>.*?</tr>", re.S | re.I)
_TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _extract_from_tables(
    page: FetchedPage, intent: str, entities: list[str]
) -> list[Candidate]:
    """Extract candidates from HTML tables.

    Each non-header row becomes at most ONE candidate per matching intent,
    formatted as ``"header: cell | header: cell"`` so the verifier can
    independently audit each cell against its header.
    """
    out: list[Candidate] = []
    if not page.html:
        return out
    for tmatch in _TABLE_RE.finditer(page.html):
        rows: list[list[str]] = []
        for rmatch in _TR_RE.finditer(tmatch.group(0)):
            cells = [
                _WS_RE.sub(" ", _TAG_RE.sub(" ", c)).strip()
                for c in _TD_RE.findall(rmatch.group(0))
            ]
            cells = [c for c in cells if c]
            if cells:
                rows.append(cells)
        if not rows:
            continue
        headers = rows[0] if rows else []
        body_rows = rows[1:]
        if not body_rows:
            continue
        for row_idx, row in enumerate(body_rows):
            row_str = " | ".join(
                f"{(headers[i] if i < len(headers) else f'col{i+1}')}: {cell}"
                for i, cell in enumerate(row)
            )
            if not _row_matches_intent(row_str, intent, entities):
                continue
            # Capture the *requested* cell when we can identify it.
            requested_cell = _pick_requested_cell(headers, row, intent, entities)
            if requested_cell is None:
                # Fall back to the full row string but still keep header context.
                value = row_str
                evidence_path = f"row[{row_idx}]"
            else:
                header, cell = requested_cell
                value = f"{header}: {cell}"
                evidence_path = f"row[{row_idx}].{_safe_col(header)}"
            # Capture the row's entity name so the verifier can do
            # proper row-level entity matching for cross-entity cases.
            row_entity = _row_entity(row, headers)
            out.append(
                Candidate(
                    value=value,
                    kind="table-row",
                    page_url=page.final_url or page.url,
                    page_title=page.title,
                    snippet=row_str,
                    structured=True,
                    hop=page.hop,
                    boost=1.5,
                    evidence_kind="table",
                    evidence_path=evidence_path,
                    evidence_text=row_str,
                    entity=row_entity,
                )
            )
    return out


_CURRENCY_TOKENS = ("USD", "EUR", "GBP", "JPY", "INR", "$", "€", "£", "¥")


def _merge_currency(value: str, row: list[str], headers: list[str]) -> str:
    """If ``value`` is a bare price-like number, look for a currency
    marker in another column of the same row and merge them so the
    verifier gets ``"Price: 1299 USD"`` rather than ``"Price: 1299"``
    (which is ambiguous for the verifier's value-in-text check).
    """
    if not value:
        return value
    # If the value already contains a currency marker, leave it alone.
    if any(c in value for c in _CURRENCY_TOKENS):
        return value
    # Look for a currency-only cell in the row.
    for cell in row:
        c = cell.strip()
        if not c:
            continue
        if c in _CURRENCY_TOKENS:
            return f"{value} {c}"
        # "EUR", "USD", etc. may appear with surrounding punctuation.
        if re.fullmatch(r"(?:US\$|USD|EUR|GBP|Rs\.?|INR|JPY|¥|\$|€|£)", c):
            return f"{value} {c}"
    return value


def _pick_requested_cell(
    headers: list[str], row: list[str], intent: str, entities: list[str]
) -> tuple[str, str] | None:
    """Return ``(header, cell)`` for the cell that best answers the intent,
    or ``None`` if no column match.

    Strategy:
      1. If an entity is mentioned anywhere in the row and the row also
         has a column matching the intent keyword, return the
         intent-column cell from THAT specific row (handled by the
         caller via entity-aware table iteration — see extract).
      2. Otherwise, prefer the first header-column match.
      3. Otherwise, prefer the cell with the longest entity name.
    """
    intent_keywords = {
        "price": ("price", "cost", "usd", "eur", "gbp", "amount", "value"),
        "spec": ("spec", "dimension", "weight", "size", "material", "value", "feature"),
        "contact": ("phone", "tel", "email", "address", "fax"),
        "hours": ("hours", "opening", "monday", "tuesday", "wednesday", "thursday",
                  "friday", "saturday", "sunday", "time", "schedule"),
        "date": ("date", "released", "launch", "available", "updated"),
    }
    keywords = intent_keywords.get(intent, ())
    # First: header column match.
    for i, cell in enumerate(row):
        if i >= len(headers):
            break
        header = headers[i]
        low = header.lower()
        if any(k in low for k in keywords):
            return header, cell
    # Second: entity match anywhere in the row -> return the cell with
    # the longest entity name (best specificity).
    best: tuple[str, str] | None = None
    best_len = -1
    for i, cell in enumerate(row):
        if i >= len(headers):
            break
        header = headers[i]
        low = cell.lower()
        for e in entities:
            if len(e) >= 3 and e.lower() in low and len(e) > best_len:
                best = (header, cell)
                best_len = len(e)
    return best


def _safe_col(s: str) -> str:
    """Make a header string safe for use in an evidence path."""
    out = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return out or "col"


def _row_entity(row: list[str], headers: list[str]) -> str:
    """Try to extract a product/entity name from a table row by
    finding the first non-numeric, non-currency cell. Used to
    populate Candidate.entity for table candidates so the verifier's
    entity gate can match by row."""
    for i, cell in enumerate(row):
        c = cell.strip()
        if not c:
            continue
        low = c.lower()
        # Skip numeric / price / currency cells.
        if re.match(r"^[\$€£¥]?\s*\d", c):
            continue
        if low in {"usd", "eur", "gbp", "jpy", "inr", "$", "€", "£", "¥"}:
            continue
        if re.fullmatch(r"\d+([.,]\d+)?", c):
            continue
        return c
    return ""


def _row_matches_intent(row: str, intent: str, entities: list[str]) -> bool:
    low = row.lower()
    if intent == "price" and any(s in low for s in ("price", "cost", "usd", "eur", "gbp")):
        return True
    if intent == "spec" and any(s in low for s in ("spec", "dimension", "weight", "size", "material")):
        return True
    if intent == "contact" and any(s in low for s in ("phone", "tel", "email", "address")):
        return True
    if intent == "hours" and any(s in low for s in ("hours", "monday", "tuesday", "opening")):
        return True
    if intent == "date" and any(s in low for s in ("date", "released", "launch", "available")):
        return True
    if not entities:
        return False
    return any(e.lower() in low for e in entities if len(e) >= 3)


# --- Plain-text intent patterns -----------------------------------------

def _extract_from_text(
    page: FetchedPage, intent: str, entities: list[str], raw_query: str
) -> list[Candidate]:
    out: list[Candidate] = []
    text = page.text or ""
    if not text:
        return out

    # Build a list of "competing" entities that are NOT in the asked
    # list but appear in the page text — used to reject candidates
    # where a competing entity is closer to the value than the asked one.
    asked_lower = {e.lower() for e in entities if len(e) >= 3}
    text_lower = text.lower()
    other_entities: list[str] = []
    # Find all multi-word capitalized entities in the text. A simple
    # heuristic: any Title-Case multi-token phrase.
    for m in re.finditer(
        r"\b[A-Z][A-Za-z0-9&.\-]*(?:[\s\-][A-Z0-9][A-Za-z0-9&.\-]*){0,3}\b",
        text,
    ):
        cand = m.group(0).strip()
        if len(cand) < 3:
            continue
        low = cand.lower()
        if low in asked_lower:
            continue
        if low in (e.lower() for e in other_entities):
            continue
        if cand.lower() in {"the", "and", "for", "with"}:
            continue
        other_entities.append(cand)

    # Pattern dispatch by intent.
    if intent == "price":
        for m in _PRICE_RE.finditer(text):
            snippet = _context(text, m.start(), m.end(), 80)
            # Find nearest entity in the snippet, scoped to the value
            # position so cross-entity collisions are avoided.
            ent = _closest_entity(
                snippet, m.group(0), entities,
                other_entities=other_entities,
            )
            out.append(_make_text_candidate(page, m.group(0), "text:price", snippet, ent))
    elif intent == "contact":
        for m in _PHONE_RE.finditer(text):
            snippet = _context(text, m.start(), m.end(), 60)
            ent = _closest_entity(
                snippet, m.group(0), entities,
                other_entities=other_entities,
            )
            out.append(_make_text_candidate(page, m.group(0), "text:phone",
                                            snippet, ent))
        for m in _EMAIL_RE.finditer(text):
            snippet = _context(text, m.start(), m.end(), 60)
            ent = _closest_entity(
                snippet, m.group(0), entities,
                other_entities=other_entities,
            )
            out.append(_make_text_candidate(page, m.group(0), "text:email",
                                            snippet, ent))
    elif intent == "hours":
        for m in _HOURS_RE.finditer(text):
            snippet = _context(text, m.start(), m.end(), 60)
            ent = _closest_entity(
                snippet, m.group(0), entities,
                other_entities=other_entities,
            )
            out.append(_make_text_candidate(page, m.group(0), "text:hours",
                                            snippet, ent))
    elif intent == "date":
        for m in _DATE_RE.finditer(text):
            snippet = _context(text, m.start(), m.end(), 60)
            ent = _closest_entity(
                snippet, m.group(0), entities,
                other_entities=other_entities,
            )
            out.append(_make_text_candidate(page, m.group(0), "text:date",
                                            snippet, ent))
    elif intent == "definition":
        # First sentence after the entity.
        if entities:
            ent = entities[0]
            for sent in re.split(r"(?<=[.!?])\s+", text):
                if ent in sent:
                    out.append(_make_text_candidate(
                        page, sent.strip(), "text:definition", sent[:200], ent
                    ))
                    break
    else:
        # General / fact / how_to / list — return entity-bearing sentences
        # ranked by entity density.
        sentences = re.split(r"(?<=[.!?])\s+", text)
        scored: list[tuple[float, str, str]] = []
        for s in sentences:
            if len(s) > 400 or len(s) < 20:
                continue
            low = s.lower()
            ent_hits = sum(1 for e in entities if e.lower() in low and len(e) >= 3)
            if ent_hits == 0:
                continue
            # Prefer sentences with a specific number/date.
            has_number = bool(re.search(r"\b\d", s))
            score = ent_hits + (0.5 if has_number else 0)
            chosen_ent = next((e for e in entities if e.lower() in low and len(e) >= 3), "")
            scored.append((score, s.strip(), chosen_ent))
        scored.sort(key=lambda x: -x[0])
        for score, s, ent in scored[:5]:
            out.append(_make_text_candidate(
                page, s, f"text:sentence(score={score:.1f})", s[:200], ent
            ))
    return out


def _closest_entity(
    snippet: str,
    value: str,
    entities: list[str],
    *,
    other_entities: list[str] = (),
) -> str:
    """Return the entity whose literal appears in the snippet NEAREST
    to the ``value``.

    Semantics:
      - Consider ONLY entities in ``entities`` (the asked entities).
      - Pick the one whose distance to the value's position is smallest.
      - If ``other_entities`` is supplied, also consider those competing
        entities. If any competing entity is closer to the value than
        the asked one, return ``""`` (the value probably belongs to the
        other entity, not the asked one).
      - Require that the picked entity is within ``~30 chars`` of the
        value. If no asked entity is near the value, return ``""`` so
        the candidate relies on page-level evidence rather than a
        possibly-misattributed entity.

    This avoids the failure mode where a page lists multiple products
    and the extractor would otherwise attribute every candidate to
    whichever asked entity happens to appear anywhere in the snippet.
    """
    if not entities or not value or not snippet:
        return ""
    pos = snippet.lower().find(value.lower())
    if pos < 0:
        pos = 0
    window = 30  # chars; tight enough that "Nimbus-7 1.299,00 EUR" pairs
    low = snippet.lower()
    best = ""
    best_dist = 10**9
    for e in entities:
        if len(e) < 3:
            continue
        idx = low.find(e.lower())
        if idx < 0:
            continue
        dist = abs(idx - pos)
        if dist < best_dist:
            best_dist = dist
            best = e
    if best_dist > window:
        return ""
    # Competing-entity check: if another entity is closer to the value
    # than the asked one, reject the association.
    if other_entities:
        other_best_dist = 10**9
        for oe in other_entities:
            if len(oe) < 3:
                continue
            oidx = low.find(oe.lower())
            if oidx < 0:
                continue
            odist = abs(oidx - pos)
            if odist < other_best_dist:
                other_best_dist = odist
        if other_best_dist < best_dist:
            return ""
    return best


def _make_text_candidate(page: FetchedPage, value: str, kind: str,
                         snippet: str, entity: str = "") -> Candidate:
    return Candidate(
        value=value,
        kind=kind,
        page_url=page.final_url or page.url,
        page_title=page.title,
        snippet=snippet,
        structured=False,
        hop=page.hop,
        boost=1.0,
        evidence_kind="text",
        evidence_text=snippet,
        entity=entity,
    )


def _context(text: str, start: int, end: int, pad: int) -> str:
    return text[max(0, start - pad): min(len(text), end + pad)].strip()[:300]