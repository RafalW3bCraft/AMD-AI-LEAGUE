# Match 4 Submission Contract — Assumptions

> The official Match 4 starter kit was not directly accessible from this
> development environment (it requires AMD SSO login at
> `notebooks.amd.com/hackathon`). The contract below is our best-effort
> inference from:
> 1. The lablab.ai league page (Match 4 brief).
> 2. The Match 3 starter kit contract (prior match, same organization,
>    same container pattern).
> 3. The first Match 4 submission (WebRetriever / ArcOmega, Oct 13 2026).
>
> If AMD's actual grader differs, the entrypoint script `app/app.py`
> detects mode from argv and degrades gracefully. Each assumption is
> tagged VERIFIED / INFERRED / UNVERIFIED.

## VERIFIED (from lablab.ai league page)
- Match title: "Intelligent Web Information Retrieval"
- One-line brief: "Build an AI agent that can navigate websites,
  understand a natural-language request and accurately retrieve the
  specific information a user needs."
- Submission form: public GitHub repo + Demo URL on lablab.ai
- Containerization: required (matches Match 2/3 mandate)
- Compute: AMD ROCm GPU via `notebooks.amd.com/hackathon` JupyterLab

## INFERRED (from Match 3 starter kit pattern + WebRetriever description)
The grader invokes the containerized app via CLI. Two-phase pattern:

```bash
# Phase 1 — startup / warmup (optional)
python3 /app/app.py --init          # or --index / --warmup

# Phase 2 — per query (fresh process each time)
python3 /app/app.py \
  --query-id <id> \
  --query "<natural-language request>" \
  [--start-url <url> ...]

# Output: /app/output/<query_id>_output.json
{
  "answer": "<verbatim value or empty>",
  "citations": ["<url>#<anchor>", ...],
  "confidence": 0.0,
  "trail":   [{"url": "...", "hop": 1, "reason": "..."}, ...],
  "reasoning": "<short justification, optional>"
}
```

## UNVERIFIED (will be confirmed on AMD notebook during ITERATION 3)
- The exact flag names the grader passes. Our entrypoint accepts
  `--query-id`, `--query`, `--start-url` (repeatable), `--corpus` (no-op
  fallback), and `--init`. Unknown flags are ignored, never crash.
- Whether the grader requires network at evaluation time. Our agent
  works in both modes: with-network (live web) and offline (returns
  refusal with explanation).
- Whether the JSON schema is exactly as shown. We write the schema above
  plus pass-through any extra fields we discover.

## Resource limits (carried over from Match 3, assumed similar)
- Container startup: ≤ 10 minutes
- Per-query latency: ≤ 30 seconds
- VRAM: 1–48 GiB (we use 0 by default; LLM optional)
- Image size: ≤ 60 GiB uncompressed
- ROCm base: `rocm/pytorch:rocm10.0_ubuntu26.04_py3.14_pytorch_release_2.13.0`

## Refusal contract (mandatory)
If no candidate answer is supported by evidence, the response MUST be:
```json
{"answer": "", "citations": [], "confidence": 0.0}
```
No prose, no half-guesses, no "I think" output. An honest refusal is
worth as much as a correct answer and worth more than a hallucinated one.

## Citation contract (mandatory)
- Every non-empty `answer` MUST have ≥ 1 citation.
- Every citation MUST be a URL that was actually fetched during the
  query (logged in the trail).
- Every citation's URL MUST contain the answer value as a substring
  of its rendered text (verified by Stage 5).
- Token-boundary regex prevents `4.3.2` falsely matching `2432` or
  `v1.2.3` falsely matching `11122`.

## What we will NOT do
- We will not invent evaluator responses.
- We will not claim AMD validation without real notebook logs.
- We will not use NVIDIA/CUDA-specific code.
- We will not hard-code answers to specific questions.