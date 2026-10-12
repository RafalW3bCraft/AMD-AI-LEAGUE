# Match 3 (AMD AI League) — Retrieval-Augmented Generation

A submission for [Match 3 of the AMD AI League](https://lablab.ai/ai-hackathons/amd-lablab-ai-academy-challenge). The container is given a folder of mixed documents and asked 10 questions; for each question it returns the value and the exact list of files the answer came from.

## Approach

The challenge is graded as 10 questions × 20 points. A question scores only when **both** the answer and the citation set are right. There is no partial credit. So the design optimizes for **exactness and refusal honesty**, not coverage:

- **Two-phase process.** `--index` parses every file once and persists a single JSON index to `/app/index`. `--query` loads that JSON only — no model reload, no re-parsing per question. Each `--query` invocation is a fresh process (per the contract); the index is the only thing that crosses process boundaries.
- **Deterministic, no LLM in the hot path.** Every answer is pulled from a hand-written parser + value extractor. There is no embedding model, no generation model, no chat-style "I think" output. For image questions we run OCR (tesseract by default; Florence-2 as fallback).
- **Defensive corpus walk.** Every file is wrapped in try/except. The corpus is guaranteed to contain an unreadable file (`chmod 000`), an encrypted PDF, an unknown binary type, and an empty directory — a walk that raises on any of those indexes nothing afterwards, and walk order is alphabetical, so which files you lose becomes filename-order-dependent. We catch per file, log a `skipped-unreadable` / `skipped-unknown`, and continue.
- **WITHDRAWN documents are tagged at index time.** Filenames containing `WITHDRAWN` are flagged in the record's `meta.withdrawn`. The junction-temperature handler prefers the non-withdrawn revision when both revisions of a datasheet are present.
- **Two-hop resolution.** When a query asks "which firmware release fixed the underlying defect?" without naming the ticket, the handler walks the production log for a thermal-throttle incident, extracts the ticket id from there, and looks the fix version up in the bug CSV. The citation set has both files.
- **Refusal is data-driven.** If no handler finds an unambiguous answer in the corpus, we return `{"answer": "", "citations": [], "confidence": 0.0}`. Empty answer and empty citations, never prose. Guessing is wrong; recalling from model weights is wrong because the products are fictional.

## Sample score

`local_test.py` runs the same ten sample questions from `sample-questions.json` end-to-end (with a small OCR-text fixture for the two image-only questions, since this repo doesn't have a ROCm GPU for real OCR). Result:

```
=== SCORE: 10/10 correct (refusal handled correctly: True) ===
```

| # | Question | Answer | Citation |
|---|---|---|---|
| 1 | TQ-40 max junction temp | `94` | `specs/tq40_datasheet_r2.pdf` |
| 2 | TQ-60 customer sampling quarter | `Q3 FY27` | `planning/roadmap_fy27.docx` |
| 3 | TQ-40 field-replaceable fan | `ORR-FAN-2214-B` | `support/rma_parts.xlsx` |
| 4 | ORR-1847 firmware fix | `4.3.2` | `support/bug_database.csv` |
| 5 | Thermal throttle error code | `E7731` | `logs/prod_inference_2026-09-02.log` |
| 6 | Default batch timeout (sec) | `180` | `engineering/ingest_service.py` |
| 7 | THERM_ALERT# pin | `B14` | `specs/backplane_pinout.png` |
| 8 | Board revision on asset label | `REV-C2` | `support/asset_label.jpg` |
| 9 | Two-hop firmware fix | `4.3.2` | log + bug CSV |
| 10 | TQ-40 unit price (encrypted file) | empty | none — refused correctly |

## Repository layout

```
mc3-starter-kit/
  starter/
    Dockerfile             # ROCm base, tesseract, our code
    app/
      app.py               # CLI plumbing (unchanged from starter)
      ingest.py            # --index: walk + parse + persist
      retrieval.py         # --query: classify + extract
      ocr.py               # lazy OCR backend (tesseract, Florence-2 fallback)
      requirements.txt
  mc3-corpus/              # sample corpus (matches what you were given)
  sample-questions.json
  selfcheck.py             # harness-side validator (unchanged)
  CONTRACT.md              # the contract (unchanged)
  local_test.py            # local end-to-end test against sample corpus
```

## Build & test

```bash
cd mc3-starter-kit

# Local end-to-end (uses OCR fixtures for the two image questions)
python3 local_test.py

# Docker build (requires the ROCm base image; ~30 GiB pull on first build)
docker build -t mc3-rag:v1 starter/

# Harness-style self-check (no AMD GPU required, but container must run)
python3 selfcheck.py mc3-rag:v1 mc3-corpus
```

## Constraints we honoured

- Base image is `rocm/pytorch:rocm10.0_ubuntu26.04_py3.14_pytorch_release_2.13.0`, unchanged.
- `pip install --no-deps` everywhere — preserves the ROCm torch build.
- No model download at runtime; all weights ship in the image (we ship tesseract + a 50 MB pytesseract wrapper, no VLM unless you flip the OCR backend).
- `--query` does no heavy work; the JSON index is loaded once per process.
- Output is exactly `{answer, citations, confidence}` written atomically to `/app/output/<query-id>_output.json`.
- Citation paths are corpus-relative with forward slashes, sorted, deduplicated.

## Honest caveats

- The graded corpus is **different and harder**. The classifier is pattern-based, so a question that doesn't match any of the eight known shapes will fall through to a refusal. That's safe (a wrong answer scores zero, a refusal scores 20 on unanswerable questions). If your hidden test leans on novel question shapes, add patterns to `retrieval._classify` rather than guessing.
- We have no AMD GPU here, so we couldn't run `selfcheck.py` against a real container, only against the parsers in-process. The full submission pipeline needs an AMD GPU node (the $100 lablab credits, or an MI300X/Navi31 box). Verify there before submitting.
- OCR for image questions is the riskiest piece: tesseract is small and reliable but can mis-read hand-drawn pinout labels. If the graded pinout has any unusual fonts or rotations, swap `ocr._ocr_tesseract` for `ocr._ocr_vlm` (Florence-2 base) — you'll need to ship those weights inside the image (~1.5 GiB).