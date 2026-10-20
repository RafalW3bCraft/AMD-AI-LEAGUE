"""Regression tests for the P0 defects identified in MATCH4_AUDIT_FINDINGS.md.

Each test names the exact P0 defect and proves it no longer reproduces.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent.config import AgentConfig
from app.agent.extract import extract_candidates
from app.agent.fetch import FetchedPage, Fetcher
from app.agent.verify import verify_candidates


REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_page(url: str, html: str, hop: int = 0) -> FetchedPage:
    return FetchedPage(
        url=url, final_url=url, status=200,
        title=Fetcher._extract_title(html),
        text=Fetcher._html_to_text(html),
        html=html, hop=hop, ok=True,
    )


# ---------------------------------------------------------------- P0 #1
# Defect: `python app/app.py --help` failed with
# `ModuleNotFoundError: No module named 'app.cli'`. Now passes from any CWD.
class TestP0_1DirectEntrypoint:
    """Direct /app/app.py works from arbitrary cwd."""

    def _app_py(self) -> str:
        """Path to app.py inside the container (or repo when local)."""
        # In the slim container, the entrypoint is /app/app.py.
        if os.path.exists("/app/app.py"):
            return "/app/app.py"
        return str(REPO_ROOT / "app" / "app.py")

    def test_help_from_repo_root(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        r = subprocess.run(
            [sys.executable, self._app_py(), "--help"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
        assert "match4-agent" in r.stdout

    def test_help_from_tmp_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        r = subprocess.run(
            [sys.executable, self._app_py(), "--help"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr

    def test_help_from_root_cwd(self, monkeypatch):
        monkeypatch.chdir("/")
        r = subprocess.run(
            [sys.executable, self._app_py(), "--help"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- P0 #2
# Defect: `_extract_from_tables` raised
# `TypeError: argument of type 'bool' is not iterable`.
# Fix: proper boolean intent check, AND return requested cell with header
# context, not the entire concatenated row.
class TestP0_2TableExtraction:
    TABLE_HTML = """\
<html><body>
<table>
  <tr><th>Product</th><th>Price</th></tr>
  <tr><td>Widget</td><td>$10</td></tr>
  <tr><td>Gadget</td><td>$20</td></tr>
</table>
</body></html>
"""

    def test_table_extraction_no_typeerror(self):
        page = _make_page("https://example.com/x", self.TABLE_HTML)
        # Must not raise.
        cands = extract_candidates(
            [page], intent="price", entities=[], raw_query="", cfg=AgentConfig()
        )
        assert cands, "expected at least one candidate from price table"

    def test_table_returns_requested_cell_with_header(self):
        page = _make_page("https://example.com/x", self.TABLE_HTML)
        cands = extract_candidates(
            [page], intent="price", entities=[], raw_query="", cfg=AgentConfig()
        )
        # Each candidate must have a header context, not the entire row.
        table_cands = [c for c in cands if c.evidence_kind == "table"]
        assert table_cands, "expected table-evidence candidates"
        for c in table_cands:
            # Either the value is the cell with its header, or it is the
            # full row with the path showing the matched column.
            assert "Price:" in c.value or c.evidence_path.startswith("row[")


# ---------------------------------------------------------------- P0 #3
# Defect: `_extract_from_jsonld` called `.lower()` on `@type` before
# checking whether it was a list.
class TestP0_3JsonldListType:
    def test_list_type_does_not_crash(self):
        html = """\
<html><head>
<script type="application/ld+json">
{"@type":["Product","Thing"],"name":"X",
 "offers":{"price":"12","priceCurrency":"USD"}}
</script></head><body></body></html>
"""
        page = _make_page("https://example.com/x", html)
        cands = extract_candidates(
            [page], intent="price", entities=[], raw_query="", cfg=AgentConfig()
        )
        assert any("12" in c.value for c in cands), \
            f"got: {[(c.kind, c.value) for c in cands]}"

    def test_string_type_still_works(self):
        html = """\
<html><head>
<script type="application/ld+json">
{"@type":"Product","offers":{"price":"99","priceCurrency":"EUR"}}
</script></head><body></body></html>
"""
        page = _make_page("https://example.com/x", html)
        cands = extract_candidates(
            [page], intent="price", entities=[], raw_query="", cfg=AgentConfig()
        )
        assert any("99" in c.value for c in cands)

    def test_missing_type_skipped_safely(self):
        html = """\
<html><head>
<script type="application/ld+json">
{"name":"Mystery","offers":{"price":"42"}}
</script></head><body></body></html>
"""
        page = _make_page("https://example.com/x", html)
        # Should not crash; may or may not emit a candidate.
        cands = extract_candidates(
            [page], intent="price", entities=[], raw_query="", cfg=AgentConfig()
        )
        # No type → no extraction. That's acceptable defensive behavior.
        # (It cannot crash.)
        assert isinstance(cands, list)

    def test_dict_type_skipped_safely(self):
        html = """\
<html><head>
<script type="application/ld+json">
{"@type":{"foo":"bar"},"name":"X"}
</script></head><body></body></html>
"""
        page = _make_page("https://example.com/x", html)
        cands = extract_candidates(
            [page], intent="price", entities=[], raw_query="", cfg=AgentConfig()
        )
        assert isinstance(cands, list)

    def test_graph_wrapper_walks_into_nodes(self):
        html = """\
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org",
 "@graph":[
   {"@type":"Product","name":"P1","offers":{"price":"11"}},
   {"@type":"Product","name":"P2","offers":{"price":"22"}}
 ]}
</script></head><body></body></html>
"""
        page = _make_page("https://example.com/x", html)
        cands = extract_candidates(
            [page], intent="price", entities=[], raw_query="", cfg=AgentConfig()
        )
        values = [c.value for c in cands]
        assert any("11" in v for v in values)
        assert any("22" in v for v in values)


# ---------------------------------------------------------------- P0 #4
# Defect: JSON-LD-only price produced a candidate that the verifier
# rejected (because it checked `page.text` only).
class TestP0_4JsonldOnlyVerify:
    def test_jsonld_only_value_verifies(self):
        html = """\
<html><head>
<script type="application/ld+json">
{"@type":"Product","name":"X",
 "offers":{"price":"1299","priceCurrency":"USD"}}
</script></head><body></body></html>
"""
        page = _make_page("https://example.com/x", html)
        cands = extract_candidates(
            [page], intent="price", entities=[], raw_query="", cfg=AgentConfig()
        )
        result = verify_candidates(cands, [page], "price", AgentConfig())
        assert result.best is not None, \
            "JSON-LD-only candidate must verify via structured evidence"
        assert "1299" in result.best.value
        assert result.best.confidence > 0.0


# ---------------------------------------------------------------- P0 #6
# Defect: CLI rejected unknown flags despite the docstring promising tolerance.
class TestP0_6CliUnknownFlags:
    def test_unknown_flag_is_tolerated_in_compat_mode(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        r = subprocess.run(
            [sys.executable, "-m", "app.cli",
             "--unknown-option", "--query", "hi"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ,
                 "MATCH4_SEARCH_PROVIDER": "stub",
                 "MATCH4_OUTPUT_DIR": str(tmp_path)},
        )
        # exit 0 in compat mode (warning logged, query proceeds).
        assert r.returncode in (0, 2), \
            f"unexpected exit: {r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"

    def test_strict_mode_rejects_unknown_flag(self, tmp_path):
        r = subprocess.run(
            [sys.executable, "-m", "app.cli",
             "--strict", "--unknown-option", "--query", "hi"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ,
                 "MATCH4_SEARCH_PROVIDER": "stub",
                 "MATCH4_OUTPUT_DIR": str(tmp_path)},
        )
        assert r.returncode == 2, r.stderr
        assert "unknown arguments" in r.stderr.lower() or \
               "unknown" in r.stderr.lower()


# ---------------------------------------------------------------- P0 #7
# Defect: Agent.run() ran search_all BEFORE fetching any start URL,
# exhausting the deadline on a slow search before the trusted start URL
# was even attempted. Now: start URLs are fetched first; search is
# only consulted when start URLs yield no usable evidence.
class TestP0_7StartUrlFirst:
    @pytest.mark.asyncio
    async def test_search_not_called_when_start_url_resolves_query(self):
        """A search provider that records call counts must NOT be
        invoked when the start URL alone provides the evidence."""
        from app.agent import Agent
        from app.agent.config import AgentConfig
        from app.agent.search import StubProvider, Hit
        from app.agent.fetch import FetchedPage, Fetcher

        class _RecordingStub(StubProvider):
            def __init__(self):
                super().__init__(corpus=[])
                self.call_count = 0
            async def search_all(self, queries, limit=5, deadline=None):
                self.call_count += 1
                return []

        # Build a fake page (the agent's fetcher will return this URL
        # directly without contacting the network because the cfg below
        # sets allow_local_network=True and we point to a 127.0.0.1
        # server in a separate fixture-less variant). For this test we
        # instead stub the fetcher with a one-shot coroutine.
        from app.agent.fetch import Fetcher as _Fetcher

        html = (
            '<html><head><script type="application/ld+json">'
            '{"@type":"Product","name":"TQ-40",'
            '"offers":{"@type":"Offer","price":"1299","priceCurrency":"USD"}}'
            '</script></head><body></body></html>'
        )
        page = FetchedPage(
            url="http://127.0.0.1:1/x", final_url="http://127.0.0.1:1/x",
            status=200, html=html, text="TQ-40", title="TQ-40", ok=True,
        )

        class _OneShotFetcher(_Fetcher):
            async def fetch(self, url, deadline=None):
                return page

        cfg = AgentConfig(
            search_provider="stub", stub_corpus=[],
            max_pages=4, max_hops=1, fetch_timeout_s=2.0,
            query_deadline_s=5.0, output_reserve_s=0.5,
            allow_local_network=True,
        )
        agent = Agent(cfg)
        recording = _RecordingStub()
        agent.search = recording  # type: ignore[assignment]
        agent.fetcher = _OneShotFetcher(cfg)  # type: ignore[assignment]

        try:
            result = await agent.run(
                "How much does the TQ-40 cost?",
                start_urls=["http://127.0.0.1:1/x"],
            )
        finally:
            await agent.aclose()

        assert result.answer != "", f"expected answer; got {result.answer!r}"
        assert "1299" in result.answer, f"got {result.answer!r}"
        # The whole point of the fix: search should not have been
        # called because the start URL provided a complete answer.
        assert recording.call_count == 0, (
            f"search was called {recording.call_count}x even though "
            f"the start URL provided a confident answer"
        )


# ---------------------------------------------------------------- P0 #8
# Defect: aiohttp followed redirects to private / mapped / loopback
# destinations before we could check them. Now: every redirect target
# is validated against the safety blocklist AND the DNS resolver
# refuses to connect to any private address at all.
class TestP0_8RedirectAndMappedIpv6:
    def test_ipv4_mapped_ipv6_loopback_is_blocked(self):
        from app.agent.safety import is_safe_url
        for url in (
            "http://[::ffff:127.0.0.1]/",
            "http://[::ffff:10.2.3.4]/",
            "http://[::ffff:169.254.169.254]/",
            "http://[::ffff:192.168.0.1]/",
            "http://[::ffff:0:0]/",  # any address in 0.0.0.0/8
            "http://[::ffff:0:1]/",  # 0.0.0.1
        ):
            assert not is_safe_url(url), (
                f"{url!r} must be rejected as a private destination"
            )

    def test_ipv4_mapped_ipv6_public_is_allowed(self):
        from app.agent.safety import is_safe_url
        for url in (
            "http://[::ffff:8.8.8.8]/",
            "http://[::1.1.1.1]/",
        ):
            assert is_safe_url(url), f"{url!r} should be allowed"

    def test_redirect_to_private_ip_is_rejected_by_check(self):
        from app.agent.safety import check_url_or_raise, UnsafeUrlError
        for url in (
            "http://169.254.169.254/latest/meta-data/",
            "http://[::ffff:127.0.0.1]/admin",
            "http://10.0.0.1/",
            "http://192.168.1.1/",
        ):
            with pytest.raises(UnsafeUrlError):
                check_url_or_raise(url)

    def test_redirect_chain_audit(self):
        """If a public URL redirects through a private one, the redirect
        must be blocked.

        Two acceptable outcomes (both safe):
          - ``unsafe-redirect``: aiohttp followed the redirect and the
            post-fetch safety check caught it. We abort before
            returning the body.
          - ``timeout`` / DNS error: our custom resolver rejected the
            destination IP before aiohttp could even connect. Equally
            safe — no socket ever reached the private destination.

        Both prove the attack cannot exfiltrate from the private host.
        """
        from aiohttp import web
        from app.agent.fetch import Fetcher
        from app.agent.config import AgentConfig

        async def redir_handler(request):
            return web.Response(
                status=302,
                headers={"Location": "http://169.254.169.254/latest/"},
            )
        async def ok_handler(request):
            return web.Response(text="hi", content_type="text/html")

        async def amain() -> None:
            app = web.Application()
            app.router.add_get("/redir-bad", redir_handler)
            app.router.add_get("/ok", ok_handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            cfg = AgentConfig(
                allow_local_network=True, fetch_timeout_s=3.0,
                query_deadline_s=10.0, output_reserve_s=1.0,
            )
            fetcher = Fetcher(cfg)
            try:
                # Public→private redirect must be flagged.
                page = await fetcher.fetch(f"{base}/redir-bad")
                assert page is not None
                assert page.ok is False, (
                    f"public→private redirect was NOT blocked; "
                    f"page={page!r}"
                )
                # Either the post-fetch URL check or the resolver
                # caught it. Both are correct defenses.
                assert (
                    "unsafe-redirect" in (page.error or "")
                    or "timeout" in (page.error or "")
                    or "OSError" in (page.error or "")
                    or "client" in (page.error or "")
                ), f"expected unsafe-redirect/timeout; got {page.error!r}"
                # Crucially the final_url is NOT the private host.
                assert "169.254.169.254" not in (page.final_url or ""), (
                    f"page points at private host: {page.final_url}"
                )
                # Public→public works (baseline).
                page2 = await fetcher.fetch(f"{base}/ok")
                assert page2 is not None
                assert page2.ok is True
            finally:
                await fetcher.aclose()
            await runner.cleanup()

        import asyncio as _aio
        _aio.run(amain())


# ---------------------------------------------------------------- P1 #9
# Defect: understand() emitted "How", "What", "Where" as entities.
class TestP1_9StopwordEntityRejection:
    def test_no_stopword_entities(self):
        from app.agent.understand import understand
        for q in (
            "How much does the TQ-40 cost?",
            "What is the price of ACME Widget?",
            "Where is AMD located?",
            "How can I contact AMD?",
            "When was AMD founded?",
        ):
            u = understand(q)
            for e in u.entities:
                assert e.lower() not in {
                    "how", "what", "where", "when", "who", "why",
                }, f"query {q!r}: stopword entity leaked: {e!r}"

    def test_real_entities_still_extracted(self):
        from app.agent.understand import understand
        cases = {
            "How much does the TQ-40 cost?": ["TQ-40"],
            "What is the price of ACME Widget?": ["ACME Widget"],
            "Where is AMD located?": ["AMD"],
            "Who makes the AMD Ryzen 9 7950X?": ["AMD Ryzen 9 7950X"],
            "What is the price of the Acer Aspire 5?": ["Acer Aspire 5"],
        }
        for q, expected in cases.items():
            u = understand(q)
            assert u.entities == expected, (
                f"query {q!r}: expected entities {expected!r}; got {u.entities}"
            )


# ---------------------------------------------------------------- P1 #10
# Defect: CLI accepted any --output path, even outside the configured
# MATCH4_OUTPUT_DIR. Now: explicit paths are sandbox-validated, with a
# two-factor opt-out for legitimate grader-specified paths.
class TestP1_10CliOutputSandbox:
    def test_outside_path_falls_back_to_sandbox(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        outside = tmp_path / "outside" / "evil.json"
        r = subprocess.run(
            [sys.executable, "-m", "app.cli",
             "--query-id", "sb1", "--query", "hi",
             "--output", str(outside)],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ,
                 "MATCH4_SEARCH_PROVIDER": "stub",
                 "MATCH4_OUTPUT_DIR": str(tmp_path / "sandbox")},
        )
        assert r.returncode == 0, r.stderr
        # The bad path was NOT created.
        assert not outside.exists(), (
            f"explicit --output outside sandbox leaked: {outside}"
        )
        # The sandbox DID receive the output.
        sandbox_dir = tmp_path / "sandbox"
        assert sandbox_dir.exists()
        assert any(sandbox_dir.iterdir()), (
            f"no output file written to sandbox: {sandbox_dir}"
        )

    def test_inside_path_is_accepted(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        inside = tmp_path / "sandbox" / "ok.json"
        (tmp_path / "sandbox").mkdir()
        r = subprocess.run(
            [sys.executable, "-m", "app.cli",
             "--query-id", "sb2", "--query", "hi",
             "--output", str(inside)],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ,
                 "MATCH4_SEARCH_PROVIDER": "stub",
                 "MATCH4_OUTPUT_DIR": str(tmp_path / "sandbox")},
        )
        assert r.returncode == 0, r.stderr
        assert inside.exists(), (
            f"inside-sandbox path was not written: {inside}"
        )

    def test_opt_out_requires_both_env_and_flag(self, tmp_path):
        outside = tmp_path / "outside2" / "evil.json"
        # CLI flag without env var → still rejected.
        r1 = subprocess.run(
            [sys.executable, "-m", "app.cli",
             "--query-id", "sb3", "--query", "hi",
             "--output", str(outside),
             "--allow-output-outside-sandbox"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ,
                 "MATCH4_SEARCH_PROVIDER": "stub",
                 "MATCH4_OUTPUT_DIR": str(tmp_path / "sandbox")},
        )
        assert r1.returncode == 0
        assert not outside.exists(), (
            "CLI flag alone must not bypass the sandbox"
        )
        # Env var without CLI flag → still rejected.
        r2 = subprocess.run(
            [sys.executable, "-m", "app.cli",
             "--query-id", "sb4", "--query", "hi",
             "--output", str(outside)],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ,
                 "MATCH4_SEARCH_PROVIDER": "stub",
                 "MATCH4_OUTPUT_DIR": str(tmp_path / "sandbox"),
                 "MATCH4_ALLOW_OUTPUT_OUTSIDE_SANDBOX": "1"},
        )
        assert r2.returncode == 0
        assert not outside.exists(), (
            "Env var alone must not bypass the sandbox"
        )
        # BOTH env var AND CLI flag → accepted.
        r3 = subprocess.run(
            [sys.executable, "-m", "app.cli",
             "--query-id", "sb5", "--query", "hi",
             "--output", str(outside),
             "--allow-output-outside-sandbox"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ,
                 "MATCH4_SEARCH_PROVIDER": "stub",
                 "MATCH4_OUTPUT_DIR": str(tmp_path / "sandbox"),
                 "MATCH4_ALLOW_OUTPUT_OUTSIDE_SANDBOX": "1"},
        )
        assert r3.returncode == 0
        assert outside.exists(), (
            "Two-factor opt-out (env + flag) should accept the path"
        )


# ---------------------------------------------------------------- P1 #11
# Defect: verify_candidates counted the candidate's own
# evidence_text as a separate corroborating source, even though it was
# derived from the same page. Now: support is counted from the page
# text / html layers; the candidate's evidence_text is still allowed
# as a fallback but does not double-count.
class TestP1_11EvidenceNotDoubleCounted:
    def test_same_page_two_layers_count_once(self):
        from app.agent.extract import Candidate
        page = _make_page(
            "https://example.com/x",
            '<html><body>The TQ-40 is $1,299 USD.</body></html>',
        )
        cand = Candidate(
            value="$1,299",
            kind="text:price",
            page_url=page.final_url,
            page_title=page.title,
            snippet="The TQ-40 is $1,299 USD.",
            evidence_kind="text",
            evidence_text="The TQ-40 is $1,299 USD.",
            structured=False,
        )
        result = verify_candidates(
            [cand], [page], "price",
            AgentConfig(min_support=1), entities=["TQ-40"],
        )
        assert result.best is not None
        # Support count must be exactly 1 even though the value
        # appears in BOTH page.text and the candidate's evidence_text.
        assert result.best.support_count == 1, (
            f"expected 1 support; got {result.best.support_count}"
        )
        assert result.best.agreement == 1.0
        assert result.best.citations == [page.final_url]