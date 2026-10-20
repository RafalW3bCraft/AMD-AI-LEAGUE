# Match 4 — Official Contract Evidence (2026-10-19)

This document captures what is officially known about the AMD AI League
Match 4 ("Intelligent Web Information Retrieval") contract, separated
from inferred/assumed behaviour inherited from Match 3 starter kits.

## 1. Public, verified context

| Source | URL | Access date | What it establishes |
|--------|-----|-------------|---------------------|
| Lablab hackathon page | https://lablab.ai/ai-hackathons/amd-lablab-ai-academy-challenge | 2026-10-19 | Match 4 dates **2026-10-13 → 2026-10-27**; individual participation; description: "Build an AI agent that can navigate websites, understand a natural-language request and accurately retrieve the specific information a user needs." |
| Lablab FAQ (linked from page) | https://lablab.ai/ai-hackathons/amd-lablab-ai-academy-challenge#faq | 2026-10-19 | AMD notebook entry point `https://notebooks.amd.com/hackathon`; ~3-hour daily running-session cap; ~25 GB persistent storage; path depends on notebook URL (`jupyter-hack-…` → `/persistent`; `rgapi-hackathon-…` → `/workspace`). |

The lablab public page does **NOT** specify:
- exact CLI invocation pattern,
- exact JSON output schema,
- exact image size limit,
- exact per-query wall-clock budget,
- scoring formula or refusal policy.

These details are intentionally left to the official Match 4 starter
kit / evaluator, which is gated behind AMD SSO and was NOT accessible
from this engineering environment.

## 2. Inferred from Match 3 (NOT verified for Match 4)

The Match 3 starter kit provided the pattern this project preserves:
- `--query-id QID --query "..." [--start-url URL]` per query,
- output JSON at `/app/output/<QID>_output.json` (or wherever
  `MATCH4_OUTPUT_DIR` points),
- `python -m app.cli --help` and `python /app/app.py --help` entrypoints,
- `MATCH4_OUTPUT_DIR` env var overrides default output directory,
- ~30 s per-query wall clock as a development target,
- refusal (empty `answer`, empty `citations`) when no evidence.

These are corroborated by the multi-source `contracts/CONTRACT.md`
documentation inside this repo but have **not** been independently
verified against a Match 4 grader. We mark every assumption as
INFERRED below.

## 3. Items by status

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1 | Match 4 dates 2026-10-13 → 2026-10-27 | **VERIFIED** | lablab public page |
| 2 | Individual participation only | **VERIFIED** | lablab public page |
| 3 | AMD GPU notebook at https://notebooks.amd.com/hackathon | **VERIFIED** | lablab FAQ |
| 4 | 3-hour daily running-session cap | **VERIFIED** | lablab FAQ |
| 5 | ~25 GB persistent storage | **VERIFIED** | lablab FAQ |
| 6 | Persistent path is `/persistent` for `jupyter-hack-` URLs | **VERIFIED** | lablab FAQ |
| 7 | Workspace path is `/workspace` for `rgapi-hackathon-` URLs | **VERIFIED** | lablab FAQ |
| 8 | CLI flags `--query-id`, `--query`, `--start-url` | **INFERRED** | Match 3 pattern; sanity-checked against iter5 harness |
| 9 | Output JSON written to `MATCH4_OUTPUT_DIR/<QID>_output.json` | **INFERRED** | Match 3 pattern; we accept `--output-dir` / `--output` as compatible extensions |
| 10 | JSON output schema (`answer`, `citations`, `confidence`, `trail`, `reasoning`, …) | **INFERRED** | This repo's design; compatible with common patterns but not verified |
| 11 | Per-query wall-clock budget ~30 s | **INFERRED** | Default `query_deadline_s=28` + 2 s reserve; configurable |
| 12 | Image size cap | **INFERRED (none published)** | Not enforced in this repo |
| 13 | Refusal semantics (empty answer + no citations) | **INFERRED** | Standard for IR graders; configurable |
| 14 | Exact scoring formula | **INFERRED (unknown)** | Common combination: exact match + citation accuracy + refusal precision |
| 15 | Network policy during grader run | **INFERRED (open)** | Public Match 4 description does not say; we ship safe-by-default (block private IPs, capped timeouts) |
| 16 | Specific AMD ROCm base image tag | **INFERRED** | `rocm/pytorch:rocm10.0_ubuntu26.04_py3.14_pytorch_release_2.13.0` per Match 3 starter kit (see `Dockerfile`) |
| 17 | Authenticated Match 4 starter kit content | **BLOCKED** | Behind AMD SSO; not accessible from this engineering environment |

## 4. Risks and mitigations

Because the exact Match 4 contract is unknown, the agent is engineered
to behave **safely** under any reasonable contract:

- **SSRF**: every URL (start URL, redirect target, in-page link,
  search hit) is validated against the IP blocklist (private,
  loopback, link-local, metadata, IPv4-mapped IPv6, etc.). A
  `safe_output_path()` enforces an output-path sandbox.
- **Deadline**: every subsystem honours a shared `Deadline`. Stub
  provider with `sleep_s=N` is used in tests to prove the budget
  cannot be blown.
- **Schema strictness**: the output JSON contains ONLY the keys
  documented in `to_contract_json()` plus internal-only `error`
  (stripped on output). Adding optional extras is non-disruptive;
  removing documented ones would break the contract.
- **No GPU claim**: we ship AMD-compatible packaging but make no
  claim of GPU workload beyond what was executed.

## 5. Required remaining human action

To upgrade every INFERRED item above to VERIFIED, an authenticated
operator must:

1. Log in to https://lablab.ai with an authorised Match 4 account.
2. Retrieve the Match 4 starter kit / grader specification.
3. Cross-reference with `contracts/CONTRACT.md` and update any
   mismatches.
4. (Optional) Run the published grader against `app/app.py` inside the
   `Dockerfile` production image and capture the receipt.

Until then, every grader-facing artefact in this repo is treated as
"**safe-by-default, contract-compatible-but-unverified**".