"""Stage 1: UNDERSTAND — parse natural-language query into intent, entities,
query variants, and focus tokens. Deterministic, no LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# Order matters — first match wins.
# NB: more-specific intents (faq, contact, hours) must come BEFORE
# catch-all intents (definition, general) so that "warranty" beats
# "what is".
_INTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("price", re.compile(
        r"\b(price|cost|how much|usd|eur|gbp|\$|€|£|"
        r"buy|purchase|order|rate|fee|charge|salary)\b",
        re.I,
    )),
    ("contact", re.compile(
        r"\b(phone|tel|telephone|fax|email|e-mail|address|"
        r"contact|reach|call|whatsapp)\b", re.I,
    )),
    ("hours", re.compile(
        r"\b(hours|opening|open|close|closed|schedule|"
        r"business hours|store hours|office hours)\b", re.I,
    )),
    ("spec", re.compile(
        r"\b(spec|specification|dimension|weight|size|"
        r"capacity|wattage|voltage|amperage|material|"
        r"model number|part number|sku)\b", re.I,
    )),
    ("faq", re.compile(
        r"\b(faq|warranty|return policy|refund policy|shipping policy|"
        r"delivery time|how long (?:does|do)|is there a)\b", re.I,
    )),
    ("how_to", re.compile(
        r"\b(how to|how do|how can|steps to|tutorial|guide)\b", re.I,
    )),
    ("date", re.compile(
        r"\b(when|date|deadline|release date|launch|schedule|"
        r"on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
        r"january|february|march|april|may|june|july|august|"
        r"september|october|november|december|\d{4})\b", re.I,
    )),
    ("location", re.compile(
        r"\b(where|location|located|address|directions|near|"
        r"store locator|find a|nearest)\b", re.I,
    )),
    ("definition", re.compile(
        r"\b(what is|what's|define|definition of|meaning of|"
        r"explain|describe)\b", re.I,
    )),
    ("list", re.compile(
        r"\b(list of|examples of|types of|kinds of|categories of|"
        r"top \d+|best \d+)\b", re.I,
    )),
]

# Synonym / rephrasing table for query variant generation.
_SYNONYMS = {
    "price": ["cost", "how much", "pricing"],
    "contact": ["phone number", "email", "get in touch"],
    "hours": ["opening hours", "store hours", "business hours"],
    "spec": ["specifications", "dimensions", "details"],
    "definition": ["meaning", "explanation", "what is"],
    "how_to": ["tutorial", "guide", "instructions"],
    "date": ["when", "release date", "deadline"],
    "location": ["where", "find", "nearest"],
    "list": ["examples", "types", "categories"],
}

# Patterns for entities we care about.
_PHONE_RE = re.compile(r"\+?\d[\d\-\s().]{6,}\d")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PRICE_RE = re.compile(r"[\$€£]\s?\d[\d,]*(?:\.\d+)?")
_DATE_RE = re.compile(
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|"
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* "
    r"\d{1,2},?\s*\d{4}\b|"
    r"\b\d{4}-\d{2}-\d{2}\b"
)
_URL_RE = re.compile(r"\b[a-z0-9-]+\.[a-z]{2,}(?:/[^\s]*)?\b", re.I)
# Proper-noun-ish runs (capitalized words, possibly with digits and
# connecting punctuation). The pattern allows:
#   - Capital-letter starts (incl. acronyms "AMD", "IBM")
#   - Optional alphanumerics, dots, dashes, ampersands inside the token
#   - Internal spaces to permit multi-token entities ("ACME Widget",
#     "AMD Ryzen 9 7950X")
#   - Stop when we hit a clearly non-entity character (comma, period,
#     hyphen between tokens with no caps, etc.)
_PROPER_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9&.\-]*(?:[\s\-][A-Z0-9][A-Za-z0-9&.\-]*)*\b"
)

_STOPWORDS = {
    "the", "a", "an", "of", "for", "to", "in", "on", "at", "by",
    "with", "and", "or", "is", "are", "was", "were", "be", "been",
    "what", "which", "who", "whom", "how", "when", "where", "why",
    "i", "you", "we", "they", "he", "she", "it", "this", "that",
    "these", "those", "my", "your", "our", "their",
    "do", "does", "did", "can", "could", "will", "would", "should",
    "me", "us", "them", "him", "her",
    "some", "any", "all", "most", "many", "much",
    "about", "into", "from", "as", "than", "so", "very",
    "please", "tell", "give", "show", "find", "get",
    "there", "here",
}

# Single-character or pure-punctuation entries that the proper-noun
# regex would otherwise pick up. Capitalised single letters ("A", "I")
# are filtered separately to keep "AMD" but drop noise.
_PROPER_NOISE = {"a", "i"}


@dataclass
class Understanding:
    intent: str = "general"
    entities: list[str] = field(default_factory=list)
    query_variants: list[str] = field(default_factory=list)
    focus_tokens: list[str] = field(default_factory=list)
    raw: str = ""

    def __post_init__(self) -> None:
        if not self.raw:
            return


def understand(query: str) -> Understanding:
    q = (query or "").strip()
    if not q:
        return Understanding(intent="general", raw=q)

    # 1. Intent classification
    intent = "general"
    for name, pat in _INTENT_PATTERNS:
        if pat.search(q):
            intent = name
            break

    # 2. Entity extraction
    entities: list[str] = []
    seen_lower: set[str] = set()

    def _add(entity: str) -> None:
        e = entity.strip().strip(".,;:?!")
        if not e or len(e) < 2:
            return
        if e.lower() in seen_lower:
            return
        seen_lower.add(e.lower())
        entities.append(e)

    for m in _PROPER_RE.finditer(q):
        tok = m.group(0)
        # Drop pure stopwords ("How", "What", "Where"…) — these are
        # the first word of the question, capitalised by convention
        # but never meaningful entities.
        if tok.lower() in _STOPWORDS:
            continue
        # Drop single-character capitalised noise ("A", "I").
        if len(tok) == 1 and tok.lower() in _PROPER_NOISE:
            continue
        # Require at least one digit OR ≥2 alphabetic chars — keeps
        # "TQ-40", "AMD", "ACME Widget" and rejects bare capitalised
        # function words that slipped through stopwords (defensive).
        alnum = sum(c.isalnum() for c in tok)
        alpha = sum(c.isalpha() for c in tok)
        if alnum < 2 or alpha < 1:
            continue
        _add(tok)
    for m in _EMAIL_RE.finditer(q):
        _add(m.group(0))
    for m in _PHONE_RE.finditer(q):
        _add(m.group(0).strip())
    for m in _URL_RE.finditer(q):
        _add(m.group(0))

    # 3. Focus tokens (non-stopword words from the query)
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]+", q.lower())
    focus_tokens = [t for t in tokens if t not in _STOPWORDS and len(t) > 2]
    # De-dup keep order
    seen_t: set[str] = set()
    focus_tokens = [t for t in focus_tokens if not (t in seen_t or seen_t.add(t))]

    # 4. Query variants
    variants = _build_variants(q, intent, entities, focus_tokens)

    return Understanding(
        intent=intent,
        entities=entities,
        query_variants=variants,
        focus_tokens=focus_tokens,
        raw=q,
    )


def _build_variants(
    query: str,
    intent: str,
    entities: list[str],
    focus_tokens: list[str],
) -> list[str]:
    """Generate 2–4 query variants for the search provider.

    Strategy:
      - Always include the original query first.
      - Add intent-specific rephrasings.
      - If entities present, add "<entity> <intent-keyword>" forms.
      - Strip stopwords for a shorter "keyword" form.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(s: str) -> None:
        s = re.sub(r"\s+", " ", s).strip()
        if not s or s.lower() in seen:
            return
        seen.add(s.lower())
        out.append(s)

    _add(query)

    # Intent rephrasings.
    if intent in _SYNONYMS:
        # Replace the trigger word, not the whole query.
        for syn in _SYNONYMS[intent][:2]:
            variant = f"{syn} {query}".strip()
            if len(variant) < len(query) + 30:
                _add(variant)

    # Entity + intent keyword.
    intent_keyword = {
        "price": "price",
        "contact": "contact",
        "hours": "hours",
        "spec": "specifications",
        "definition": "definition",
        "how_to": "how to",
        "date": "release date",
        "location": "location",
        "list": "list",
    }.get(intent, "")
    if entities and intent_keyword:
        _add(f"{entities[0]} {intent_keyword}")

    # Keyword (focus tokens only).
    if focus_tokens:
        _add(" ".join(focus_tokens[:6]))

    # Cap to 4 variants to keep search budget tight.
    return out[:4]