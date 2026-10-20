# Match 4 — Performance and Security Report (2026-10-19, iter6)

## 1. Performance

### Local benchmark (Python 3.14.7, no network, stub search)
- Latency for 20 consecutive `agent.run("What is the price of TQ-40?")`
  calls with empty corpus and stub provider:
  - **min**: 0.1 ms
  - **median**: 0.1 ms
  - **p95**: 0.2 ms
  - **max**: 0.6 ms
- The default deadline (28 s) is never approached in offline mode.

### Held-out suite runtime
- `tests/test_iter6_heldout.py` (17 cases) finishes in **<1 s** end-to-end
  on the held-out fixture server (in-process aiohttp, stub search).

### Full test suite runtime
- `python -m pytest -q` runs **166 tests in ~45 s** on this host.

### Deadline propagation evidence
- `tests/test_iter3_security.py::TestP0_12SearchDeadlinePropagation::test_stub_provider_respects_deadline`
  proves that a search provider with `sleep_s=10` returns in **<1 s**
  when the deadline is exhausted.
- `tests/test_iter3_security.py::TestP0_13AgentEndToEndDeadline::test_agent_completes_within_deadline_when_search_sleeps`
  proves that an agent with a 30-second search sleep completes in
  **<10 s** end-to-end.

## 2. Security

### SSRF defence — verified

| Vector | Defended by | Test |
|--------|-------------|------|
| Literal IP (`http://127.0.0.1/`) | `safety.is_safe_url` blocklist | `tests/test_iter3_security.py::TestPrivateNetworkBlocked::test_loopback_blocked` |
| Literal private IP (`http://10.0.0.1/`) | `safety.is_safe_url` blocklist | `tests/test_iter3_security.py::TestPrivateNetworkBlocked::test_rfc1918_blocked` |
| Literal metadata IP (`http://169.254.169.254/`) | `safety.is_safe_url` blocklist | `tests/test_iter3_security.py::TestPrivateNetworkBlocked::test_metadata_blocked` |
| **IPv4-mapped IPv6** (`http://[::ffff:127.0.0.1]/`) | `safety._normalize_ip` collapses to IPv4 then re-checks | `tests/test_p0_defects.py::TestP0_8RedirectAndMappedIpv6::test_ipv4_mapped_ipv6_loopback_is_blocked` |
| URL with userinfo (`http://user:pass@host/`) | `safety.is_safe_url` rejects `@` in netloc | existing tests |
| Disallowed scheme (`ftp://`) | `safety.is_safe_url` allowlist | existing tests |
| Hostname resolving to private IP | `safety.is_safe_url` resolves via `getaddrinfo` and checks each address | `tests/test_iter3_security.py` |
| **DNS rebinding** (hostname resolves differently between check and connect) | `Fetcher._get_connector` installs a custom resolver that re-validates every IP at connection time and raises `OSError` | `tests/test_p0_defects.py::TestP0_8RedirectAndMappedIpv6::test_redirect_chain_audit` |
| **Public→private redirect** (`Location: http://169.254.169.254/`) | `Fetcher._fetch_inner` audits `resp.history` AND `resp.url` | `tests/test_p0_defects.py::TestP0_8RedirectAndMappedIpv6::test_redirect_chain_audit` |

### Output-path sandbox — verified

| Vector | Defended by | Test |
|--------|-------------|------|
| `--output /tmp/evil.json` (outside sandbox) | `cli._output_path` runs through `safe_output_path`, falls back to `MATCH4_OUTPUT_DIR` | `tests/test_p0_defects.py::TestP1_10CliOutputSandbox::test_outside_path_falls_back_to_sandbox` |
| `--output` inside sandbox | accepted | `tests/test_p0_defects.py::TestP1_10CliOutputSandbox::test_inside_path_is_accepted` |
| CLI flag `--allow-output-outside-sandbox` without env var | rejected | `tests/test_p0_defects.py::TestP1_10CliOutputSandbox::test_opt_out_requires_both_env_and_flag` |
| Env var without CLI flag | rejected | same test |
| Both env + flag | accepted | same test |
| `..` traversal | resolved by `safe_output_path` | `tests/test_iter3_security.py::TestOutputPathSafety::test_safe_output_path_relative_traversal_blocked` |
| `/tmp` by default (was too permissive) | `/tmp` removed from default allowlist | `tests/test_iter3_security.py::TestOutputPathSafety::test_safe_output_path_in_tmp_blocked_by_default` |

### Resource limits

| Resource | Limit | Configurable | Test |
|----------|-------|--------------|------|
| HTML body size | 5 MB default (`max_html_bytes`) | yes | implicit via cap enforcement |
| Rendered text size | 200 KB default (`max_text_chars`) | yes | implicit |
| Pages per query | 8 default (`max_pages`) | yes | enforced in `Agent._drain_frontier` |
| Hops per query | 2 default (`max_hops`) | yes | enforced |
| Concurrent fetches | 4 default (`max_concurrent_fetches`) | yes | semaphore in `Fetcher._get_sem` |
| Per-fetch timeout | 8 s default (`fetch_timeout_s`) | yes | `ClientTimeout(total=...)` |
| Total query deadline | 28 s default (`query_deadline_s`) | yes | `Deadline(total_s=...)` |
| Output writing reserve | 2 s default (`output_reserve_s`) | yes | `Deadline.__post_init__` |

### Prompt-injection defence

- HTML stripping removes `<script>`, `<style>`, `<noscript>`, hidden
  elements (`display:none`, `visibility:hidden`, `hidden` attribute),
  and `<meta http-equiv="refresh">` redirects before extraction.
- `flag_injection()` returns a list of detected patterns; the agent
  records the count in `result.reasoning` for transparency.
- The agent does NOT execute any code or follow `javascript:` /
  `data:` URLs.

## 3. Open items (carried to `REMAINING_ISSUES.md`)

- Full ROCm production container build is blocked on bandwidth
  (`reports/AMD_EXECUTION_EVIDENCE.md`).
- Official grader schema is INFERRED — closure requires lablab SSO
  (`reports/OFFICIAL_CONTRACT_EVIDENCE.md`).
- Optional LLM hook is documented but unused (`MATCH4_USE_LLM`); no
  GPU-side inference is claimed.