# Match 4 — AMD AI League: Intelligent Web Information Retrieval

Deterministic, no-LLM-by-default web retrieval agent. Five-stage pipeline:

    Understand  ->  Search  ->  Fetch+Navigate  ->  Extract  ->  Verify

The Verify stage is mandatory: every non-empty answer must literally appear
in a fetched page's text (token-boundary regex), or the agent refuses with
`{"answer": "", "citations": [], "confidence": 0.0}`. An honest refusal
scores higher than a hallucinated guess.

## Repository layout

    app/
      app.py              # canonical AMD grader entrypoint (/app/app.py)
      cli.py              # CLI: --init / --query-id / --query / --start-url
      server.py           # optional daemon (no-op for the deterministic core)
      agent/
        __init__.py       # Agent orchestrator + AgentResult
        config.py         # env-overridable AgentConfig
        understand.py     # Stage 1: intent, entities, query variants
        search.py         # Stage 2: DuckDuckGo HTML + offline stub
        fetch.py          # Stage 3: aiohttp fetcher + link extraction
        extract.py        # Stage 4: JSON-LD, tables, intent regexes
        verify.py         # Stage 5: token-boundary verification + ranking
    tests/                # 31 tests, deterministic, no network required
    contracts/CONTRACT.md # explicit assumptions about the grader interface
    docs/ARCHITECTURE.md  # full design + verification plan
    notebooks/match4_amd_test.ipynb  # AMD notebook validation suite
    Dockerfile            # ROCm 10.0 base; no CUDA code
    requirements.txt      # aiohttp, beautifulsoup4, lxml (pinned)

## Grader contract

    # Phase 1 (optional warmup)
    python3 /app/app.py --init

    # Phase 2 (per query, fresh process)
    python3 /app/app.py \
        --query-id query_01 \
        --query "What is the price of the TQ-40?" \
        [--start-url https://example.com ...]

    # Output: /app/output/query_01_output.json
    {
      "answer": "...",            # verbatim value or empty string
      "citations": ["https://...", ...],
      "confidence": 0.0,
      "trail": [{"url": "...", "hop": 1, "reason": "..."}, ...],
      "reasoning": "...",
      "intent": "price",
      "entities": ["..."],
      "elapsed_ms": 0,
      "pages_fetched": 0
    }

Refusal contract (mandatory): when no candidate is supported by source
text, the agent returns `{"answer": "", "citations": [], "confidence": 0.0}`.
No prose, no half-guesses.

## Local development

    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt pytest pytest-asyncio

    # Run the test suite (offline, deterministic, no network)
    pytest tests/

    # Run a live query (requires outbound HTTP)
    python -m app.cli --query-id q1 --query "What is the AMD headquarters address?"

    # Use the offline stub provider (no network at all)
    MATCH4_SEARCH_PROVIDER=stub python -m app.cli --query-id q1 --query "..."

## Configuration (env vars)

    MATCH4_OUTPUT_DIR         # default /app/output, falls back to a tmp dir
    MATCH4_SEARCH_PROVIDER    # duckduckgo (default) | stub
    MATCH4_SEARCH_TOPK        # hits per variant, default 6
    MATCH4_MAX_PAGES          # hard cap per query, default 8
    MATCH4_MAX_HOPS           # in-page link follow depth, default 2
    MATCH4_FETCH_TIMEOUT_S    # per-request timeout, default 8
    MATCH4_RESPECT_ROBOTS     # 1 (default) | 0
    MATCH4_USE_LLM            # opt-in LLM augmentation (default off)
    MATCH4_LLM_ENDPOINT       # local LLM endpoint when USE_LLM=1

## Docker

    docker build -t match4-amd-agent .
    docker run --rm match4-amd-agent --init
    docker run --rm match4-amd-agent \
        --query-id q1 --query "What is the price of the TQ-40?"

The base image is `rocm/pytorch:rocm10.0_ubuntu26.04_py3.14_pytorch_release_2.13.0`.
We do NOT `pip install torch` — the ROCm build ships in the base image and
pip would silently replace it with a CUDA build (Match 3 lesson).

## Validation on the AMD notebook

Open `notebooks/match4_amd_test.ipynb` on `notebooks.amd.com/hackathon` and
run the cells top-to-bottom. The final cell prints a VERIFIED / FAILED /
UNTESTED checklist you can paste into your submission notes.

## What we will NOT do (per contracts/CONTRACT.md)

- We will not invent evaluator responses.
- We will not claim AMD validation without real notebook logs.
- We will not use NVIDIA/CUDA-specific code.
- We will not hard-code answers to specific questions.
