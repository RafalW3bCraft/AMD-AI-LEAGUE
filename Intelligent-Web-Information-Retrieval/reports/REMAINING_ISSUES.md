# Match 4 — Remaining issues (2026-10-19, post iter6)

Status legend:
- **FIXED**: closed in this iteration, regression test in place.
- **OPEN**: known issue, workaround documented, requires grader data to close.
- **BLOCKED**: requires external access (AMD SSO, lablab submission portal,
  GitHub repo authorisation, etc.) that this engineering host cannot obtain.

## P0 / P1 defects from MATCH4_POST_HERMES_AUDIT_2026-10-19.md

| ID | Severity | Title | Status | Verification |
|----|----------|-------|--------|--------------|
| P0-A | BLOCKER | Reproducibility test compared wall-clock strings | **FIXED** | `tests/test_iter5_audit.py::TestReproducibility::test_pytest_reproducible` now parses semantic counts (`passed`/`failed`/`error`/`skipped`) and ignores timing. |
| P0-B | BLOCKER | `Agent.run` searches BEFORE start URLs | **FIXED** | `tests/test_p0_defects.py::TestP0_7StartUrlFirst::test_search_not_called_when_start_url_resolves_query`. Search is now the fallback only when start URLs fail or yield no entity-relevant evidence. |
| P0-C | BLOCKER | Redirect / DNS-rebinding TOCTOU | **FIXED** | (a) `Fetcher._fetch_inner` audits every `resp.history` entry, (b) `Fetcher._get_connector` installs a custom resolver that refuses to connect to any private/mapped IP. Tests: `TestP0_8RedirectAndMappedIpv6::test_redirect_chain_audit` and friends. |
| P0-D | BLOCKER | IPv4-mapped IPv6 bypass | **FIXED** | `app/agent/safety.py::_normalize_ip` collapses IPv4-mapped IPv6 to its embedded IPv4 before blocklist check. New test: `TestP0_8RedirectAndMappedIpv6::test_ipv4_mapped_ipv6_loopback_is_blocked`. |
| P0-E | BLOCKER | Global deadline missing from `search.search_all` | **FIXED** | `SearchProvider.search_all` and `DuckDuckGoProvider.search` now accept a `deadline` kwarg; `Agent._fetch_pipeline` propagates it. Tests: `TestP0_12SearchDeadlinePropagation`, `TestP0_13AgentEndToEndDeadline`. |
| P0-F | BLOCKER | Official contract unknown | **OPEN** | `reports/OFFICIAL_CONTRACT_EVIDENCE.md` enumerates VERIFIED vs INFERRED items. Lablab SSO is required to close. |
| P0-G | BLOCKER | Full ROCm production container build/run | **BLOCKED** | `reports/AMD_EXECUTION_EVIDENCE.md`. ROCm base image is too large to pull from this host. Slim image builds successfully. |
| P1-H | HIGH | CLI `_output_path` ignored sandbox | **FIXED** | `app/cli.py::_output_path` now runs explicit `--output` through `safe_output_path`; sandbox can only be bypassed with BOTH `MATCH4_ALLOW_OUTPUT_OUTSIDE_SANDBOX=1` AND `--allow-output-outside-sandbox`. Tests: `TestP1_10CliOutputSandbox::test_outside_path_falls_back_to_sandbox`, `test_opt_out_requires_both_env_and_flag`. |
| P1-I | HIGH | Stopword entities ("How", "What", "Where") | **FIXED** | `app/agent/understand.py` now drops stopwords AND single-letter capitals AND requires at least 2 alphanumeric chars. Tests: `TestP1_9StopwordEntityRejection`. |
| P1-J | HIGH | Evidence-counting over-counts candidate's own evidence | **FIXED** | `app/agent/verify.py::_collect_support` already deduped by `agreement_pages`; new test `TestP1_11EvidenceNotDoubleCounted::test_same_page_two_layers_count_once` proves the contract. |
| P1-K | MEDIUM | Stale docs (31-test counts, missing `FINAL_RELEASE_AUDIT.md`) | **FIXED** | All reports regenerated; `reports/FINAL_RELEASE_AUDIT.md` now exists. Test count is 166 (was 31, then 126). |
| P1-L | MEDIUM | No LICENSE, no `.dockerignore`, build caches in tree | **FIXED** | `LICENSE` (Apache-2.0) and `.dockerignore` added; build caches excluded from `.gitignore`. |
| P1-M | LOW | Optional LLM claim unsubstantiated | **OPEN** | No code change. The MATCH4_USE_LLM hook is documented in README; we do not download models in this build. Closed via docs honesty in `OFFICIAL_CONTRACT_EVIDENCE.md`. |
| P1-N | MEDIUM | `requirements.txt` drifts from installed wheels | **OPEN** | `requirements.txt` pins `aiohttp==3.10.10`, `beautifulsoup4==4.12.3`, `lxml==5.3.0`. The host has `3.14.5`, `4.15.0`, `6.1.3` respectively. Tests pass under either set; ROCm torch is preserved by NOT including `torch` in `requirements.txt`. Recommend re-pinning after Match 4 starter-kit verification. |
| P1-O | MEDIUM | Navigation / extraction resilience | **PARTIAL** | The held-out suite (`tests/test_iter6_heldout.py`, 17 cases) covers pagination, table layouts, JSON-LD graph, conflict SKUs, FAQ pages, hidden decoys, empty pages. Some real-world edges (full JS-only pages, captcha-walled pages) still require a real browser; not in scope without an LLM. |
| P1-P | MEDIUM | Output schema strictness | **OPEN** | `to_contract_json()` produces the documented set of keys; an extra `--error` field is stripped. The grader may demand an exact schema — verification deferred until P0-F closes. |

## New fixes introduced by iter6 (not in the original audit)

- **EUR continental price normalisation** (`app/agent/extract.py::_PRICE_RE`,
  `extract.py::_merge_currency`) — accepts `1.299,00 EUR` form.
- **Cross-entity disambiguation in plain-text extraction**
  (`extract.py::_closest_entity` with `other_entities` parameter) —
  prevents attributing the wrong product's price to the asked entity.
- **FAQ / definition / general prefers shorter answers**
  (`verify.py::_key` `length_sign` branch) — direct snippet wins over
  the "longer is better" heuristic used for fact/price queries.
- **Table candidates carry `entity` from the row name**
  (`extract.py::_row_entity`) — verifier's entity gate no longer
  drops table candidates just because the price cell doesn't mention
  the product name.
- **HTML aiohttp `charset=` parameter** — work around aiohttp ≥3.11
  rejecting `content_type="text/html; charset=utf-8"`.

## Test summary

```
166 passed in 45.45s (no failures, no errors)
- test_agent_e2e.py: 2
- test_extract_verify.py: 12
- test_fetch.py: 4
- test_iter2_benchmark.py: 11 (truth cases, 100% exact, 100% refusal_precision)
- test_iter2_fixtures.py: 13
- test_iter3_security.py: 31
- test_iter4_packaging.py: 19 (including 8 new Dockerfile static checks)
- test_iter5_audit.py: 12 (audit + reproducibility + diff scan + hygiene)
- test_iter6_heldout.py: 17 (NEW held-out suite)
- test_notebook_validity.py: 8
- test_p0_defects.py: 24 (11 original + 13 new regressions)
- test_understand.py: 13
```

## Outstanding human actions

Ordered by urgency:

1. **(P0-F) Authenticate to lablab.ai and retrieve the official Match 4
   starter kit / grader specification.** Until this happens we cannot
   certify JSON-schema or scoring compatibility.
2. **(P0-G) On https://notebooks.amd.com/hackathon: build the production
   `Dockerfile` and capture `rocminfo` / a real smoke-query output.**
3. **(P1-K) Authorise a public GitHub repository** for the team to push
   the submission. Until pushed, no `git push`-based external reviewer
   can verify the artifact.
4. **(P1-M) Decide whether to keep or remove the `MATCH4_USE_LLM` claim**
   in README. Currently we ship an optional LLM hook that is unused.
5. **(P1-N) Reconcile `requirements.txt` with the actual Match 4 base
   image once P0-F closes.**