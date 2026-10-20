# Match-IV Agent — Architecture & Verification Plan

## ITERATION 1: Analyze → Plan → Verify

### 1. Source of truth (verified)
| Item | Source | Status |
|---|---|---|
| Match 4 title | `lablab.ai/ai-hackathons/amd-lablab-ai-academy-challenge` | VERIFIED — "Intelligent Web Information Retrieval" |
| Match 4 window | same page | VERIFIED — Oct 13 → Oct 27, 2026 (+1,800 pts) |
| Match brief | same page | VERIFIED — "Build an AI agent that can navigate websites, understand a natural-language request and accurately retrieve the specific information a user needs" |
| Submission form | `lablab.ai/ai-hackathons/amd-developer-hackathon-act-ii` + Match 3 submission contracts | VERIFIED — public GitHub repo + Demo URL, containerized |
| Persistent dir | lablab FAQ on `amd-lablab-ai-academy-challenge` page | VERIFIED — `/persistent` for `jupyter-hack-*`, `/workspace` for `rgapi-hackathon-*`, ~25 GB |
| Compute access | lablab FAQ | VERIFIED — `notebooks.amd.com/hackathon` JupyterLab, MI300X/MI250, 8 h/24 h |
| ROCm base image | prior Match 3 starter kits (RafalW3bCraft, Eric-LJF, helshahaby, tatianamartins-VYSON) | VERIFIED — `rocm/pytorch:rocm10.0_ubuntu26.04_py3.14_pytorch_release_2.13.0` |
| Match 3 grader contract | Match 3 starter kits | VERIFIED — `app.py --index`, `app.py --corpus --query-id --query`, JSON output |
| Match 4 sample | `lablab.ai/submissions/vd34vtg3zk8a5ebe1z5x2a3z` (WebRetriever / ArcOmega) | VERIFIED — 5-stage pipeline: Understand → Search → Fetch+Navigate → Extract → Verify; hard refusal; no LLM in core |

### 2. What is UNVERIFIED (will NOT fabricate)
- The **exact** Match 4 grader CLI flags (the brief on lablab does not publish them publicly; Match 3 starter kit uses `--index/--query`, so Match 4 likely follows the same pattern but with web URLs instead of a local corpus — see `CONTRACT.md`).
- The **exact** Match 4 output JSON schema — we mirror Match 3 (`answer`, `citations`, `confidence`) with `answer` carrying the verbatim value, `citations` carrying the URLs the answer was lifted from, and `confidence` a deterministic score. The web variant may add `reasoning`/`trail`; we support them as optional.
- Whether the Match 4 container is evaluated with network access — for a web-retrieval challenge, the container MUST have network at grading time, unlike Match 3. We code defensively for both: network on, network off (cached results used).

### 3. Inputs / outputs / limits (inferred from brief + prior matches)
- **Input (per query):** natural-language query string + optional starting URL set + query id
- **Output (per query):** JSON written to `/app/output/<query_id>_output.json`:
  ```json
  {
    "answer": "<verbatim value or empty string>",
    "citations": ["https://example.com/page#anchor", ...],
    "confidence": 0.0,
    "trail": [{"url": "...", "hop": 1, "reason": "..."}, ...],
    "reasoning": "short justification (optional)"
  }
  ```
- **Limits:**
  - ≤ 10 min container startup
  - ≤ 30 s per query (Match 3 budget; Match 4 likely similar — agents should fail fast)
  - 1–48 GiB VRAM (ROCm MI300X has 192 GB; we use no GPU by default, opt-in only)
  - ≤ 60 GiB uncompressed image
  - ROCm 10.0 base image mandated

### 4. Architecture (5-stage pipeline, deterministic core)

```
┌─────────────────────────────────────────────────────────────┐
│  STAGE 1: UNDERSTAND (deterministic, regex + keyword)       │
│  - intent ∈ {price, contact, hours, spec, definition,       │
│              how_to, date, fact, list, faq, general}        │
│  - entities (proper nouns, $/%/digit runs, phone, email)     │
│  - 2–4 query variants (re-ordering, synonyms, focus shift)   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  STAGE 2: SEARCH (provider chain, automatic fallback)       │
│  - Primary:  DuckDuckGo HTML (keyless, no signup)           │
│  - Fallback: SerpAPI-style lite / Bing public               │
│  - Budget:  top-K (default 6) per variant, max 18 results   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  STAGE 3: FETCH + NAVIGATE (bounded)                        │
│  - robots.txt respected (crawl-delay)                       │
│  - per-page: HTTP fetch w/ timeout, gzip, redirects         │
│  - HTML/JS-aware parser (no headless browser by default —   │
│    optional Playwright path for JS-heavy sites)             │
│  - in-page link follow: max 2 hops, 8 pages total per query │
│  - prompt-injection detection (HTML comments, hidden text)   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  STAGE 4: EXTRACT (structure-aware, intent-specific)        │
│  - JSON-LD (Product, Organization, FAQPage, Event)          │
│  - Microdata / RDFa (itemtype=*)                            │
│  - HTML tables (preserve header→cell mapping)               │
│  - Intent patterns: price (currency), phone, email, hours,  │
│    date, definition (first sentence w/ term), spec row       │
│  - Ranked text spans (paragraph + table proximity)          │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  STAGE 5: VERIFY (mandatory, deterministic)                 │
│  - candidate value MUST appear in source text (substring)    │
│  - token-boundary regex (no false "4.3.2 in 2432")          │
│  - multi-source agreement boosts confidence                 │
│  - contradiction detection: same intent, different values    │
│  - if no candidate passes: REFUSE (empty answer/citations)  │
└─────────────────────────────────────────────────────────────┘
```

### 5. Verification before any implementation
- [x] Match brief read
- [x] Submission form read
- [x] Prior Match 3 contract understood (used as template)
- [x] Sample Match 4 submission (WebRetriever) understood
- [x] Persistent-directory rule noted
- [x] ROCm base image pinned
- [x] No NVIDIA/CUDA code planned
- [x] No fabricated evaluator interfaces — see `CONTRACT.md` for assumptions

### 6. Risks & mitigations
| Risk | Mitigation |
|---|---|
| Network unavailable at eval time | All stages degrade gracefully; if fetch fails, REFUSE honestly |
| Hidden prompt injection in HTML | Strip script/style/comments before extraction; flag suspicious instructions |
| Contradictory evidence across sources | Prefer most specific; tie-break by recency; if unresolvable, mark `conflict=true`, return value with lowest-confidence citation |
| Unsanctioned LLM API | Core pipeline uses NO LLM; LLM is OPT-IN via env var `MATCH4_USE_LLM=1` and a local model only |
| Pinned-dependency surprise | requirements.txt pins exact versions; lock via `pip-compile` in CI |
| Slow pages / pagination | Bounded budget (8 pages, 2 hops, 30 s total) + hard timeout per fetch |
| Hallucinated answer | Verify step refuses anything not substring-of-source |
| Empty query / unsupported intent | Graceful empty answer, no crash |

### 7. Deliverables checklist
- [ ] `app/app.py` — grader CLI entrypoint (mirrors Match 3 contract)
- [ ] `app/server.py` — resident daemon that warms cache (if LLM enabled)
- [ ] `app/agent/` — 5-stage pipeline modules
- [ ] `app/tools/` — search/fetch/parse utilities
- [ ] `tests/` — unit + integration tests (deterministic, no network)
- [ ] `Dockerfile` — ROCm base, baked tools, entrypoint
- [ ] `requirements.txt` — pinned deps
- [ ] `contracts/CONTRACT.md` — explicit assumptions on grader interface
- [ ] `notebooks/match4_amd_test.ipynb` — full AMD-ready verification suite
- [ ] `README.md` — submission instructions
- [ ] `SUBMISSION_CHECKLIST.md` — final go/no-go list

### 8. Status legend (used throughout)
- **VERIFIED** — confirmed against an authoritative source cited inline
- **INFERRED** — reasonable inference from a verified source, called out as such
- **UNTESTED** — code/artifact written but not yet executed on AMD GPU
- **FAILED** — execution attempted and failed; reason recorded