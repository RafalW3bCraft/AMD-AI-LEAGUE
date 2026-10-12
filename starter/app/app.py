#!/usr/bin/env python3
"""Match 3 submission — implementation of the contract.

Two phases:
  1. --index <corpus>:  walk, parse, persist evidence to INDEX_DIR (/app/index).
  2. --query <args>:    load the persisted index, classify the query, answer.

This file keeps the starter's CLI plumbing EXACTLY. Only index() and
answer() are filled in. See CONTRACT.md for the rules this implements.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

OUTPUT_DIR = Path(os.environ.get("MC2_OUTPUT_DIR", "/app/output"))
INDEX_DIR = Path(os.environ.get("MC3_INDEX_DIR", "/app/index"))


def index(corpus: Path) -> None:
    """Phase 1: parse everything once, write a single JSON index to disk.

    Charged to the startup budget (10 min). Per-question processes later
    load this JSON -- they do NOT re-parse anything.
    """
    import sys as _sys
    _here = Path(__file__).resolve().parent
    if str(_here) not in _sys.path:
        _sys.path.insert(0, str(_here))
    import ingest as _ingest
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    def _progress(n: int, p: str, status: str) -> None:
        # quiet by default; flip MC3_VERBOSE=1 for debugging
        if os.environ.get("MC3_VERBOSE"):
            print(f"  [{n:03d}] {status:24s} {p}", file=sys.stderr)

    out = _ingest.build_index(corpus, INDEX_DIR, on_progress=_progress)
    if os.environ.get("MC3_VERBOSE"):
        print(f"index wrote {out}", file=sys.stderr)


def answer(corpus: Path, query: str) -> tuple[str, list[str], float]:
    """Phase 2: thin client. Loads the persisted index, classifies, answers."""
    import sys as _sys
    _here = Path(__file__).resolve().parent
    if str(_here) not in _sys.path:
        _sys.path.insert(0, str(_here))
    import ingest as _ingest
    import retrieval as _retrieval
    import ocr as _ocr
    index_data = _ingest.load_index(INDEX_DIR)

    corpus_root = corpus.resolve()

    def _ocr_for(rel_path: str) -> str:
        try:
            return _ocr.ocr_for_corpus(corpus_root, rel_path)
        except Exception:
            return ""

    try:
        ans, cites, conf = _retrieval.answer_query(index_data, query, ocr_fn=_ocr_for)
    except Exception:
        # safety net: return a clean refusal
        return "", [], 0.0

    # persist OCR results back to the index so subsequent queries skip the model
    try:
        any_ocr = any(r.get("meta", {}).get("ocr_done") for r in index_data["files"])
        if any_ocr:
            _ingest.save_index(INDEX_DIR, index_data)
    except Exception:
        pass

    return ans, cites, conf


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=Path, help="build the index over this corpus, then exit")
    ap.add_argument("--corpus", type=Path, help="corpus root for a query")
    ap.add_argument("--query-id", help="output stem the harness assigns, e.g. query_01")
    ap.add_argument("--query", help="the question to answer")
    args = ap.parse_args()

    if args.index is not None:
        index(args.index)
        return 0

    if args.corpus is None or args.query is None or not args.query_id:
        ap.error("a query needs --corpus, --query-id and --query")

    text, citations, confidence = answer(args.corpus, args.query)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / (args.query_id + "_output.json")
    out.write_text(
        json.dumps(
            {"answer": text, "citations": list(citations), "confidence": confidence},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())