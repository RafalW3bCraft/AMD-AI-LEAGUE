"""Tests for Stage 1 — UNDERSTAND."""

from app.agent.understand import understand


def test_intent_price():
    u = understand("How much does the TQ-40 cost?")
    assert u.intent == "price"
    assert "TQ-40" in u.entities
    assert len(u.query_variants) >= 2


def test_intent_contact():
    u = understand("What's the phone number for AMD support?")
    assert u.intent == "contact"
    assert "AMD" in u.entities


def test_intent_hours():
    u = understand("What are the store hours on Saturday?")
    assert u.intent == "hours"


def test_intent_spec():
    u = understand("What is the weight of the TQ-60?")
    assert u.intent == "spec"


def test_intent_definition():
    u = understand("What is ROCm?")
    assert u.intent == "definition"


def test_intent_howto():
    u = understand("How do I install ROCm on Ubuntu?")
    assert u.intent == "how_to"


def test_intent_date():
    u = understand("When was the TQ-40 released?")
    assert u.intent == "date"


def test_intent_location():
    u = understand("Where is the nearest AMD office?")
    assert u.intent == "location"


def test_intent_general():
    u = understand("Tell me about the AMD Instinct MI300X")
    assert u.intent == "general"


def test_empty_query():
    u = understand("")
    assert u.intent == "general"
    assert u.entities == []


def test_query_variants_unique():
    u = understand("How much is the TQ-40?")
    seen = set()
    for v in u.query_variants:
        assert v.lower() not in seen
        seen.add(v.lower())


def test_email_entity():
    u = understand("Contact support@example.com for help")
    assert any("@" in e for e in u.entities)


def test_phone_entity():
    u = understand("Call +1 (555) 123-4567 for sales")
    assert any(any(c.isdigit() for c in e) and len(e) > 6 for e in u.entities)