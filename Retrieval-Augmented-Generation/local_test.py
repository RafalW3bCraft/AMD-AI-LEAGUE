#!/usr/bin/env python3
"""Local end-to-end test for the Match 3 implementation.

Runs the --index pass and then each of the ten sample questions through
--query, scoring answer + citation set against sample-questions.json.

This is NOT a substitute for the real selfcheck.py (which spins up Docker)
but it proves the contract logic works against the sample corpus.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path("/home/sp3ct0r/Desktop/M3-Retrieval-Augmented-Generation")
KIT = REPO / "mc3-starter-kit"
CORPUS = KIT / "mc3-corpus"
APP = KIT / "starter" / "app" / "app.py"
QUESTIONS = KIT / "sample-questions.json"


def normalize_answer(s: str) -> str:
    """Mirror the grader's normalization rules."""
    if s is None:
        return ""
    s = str(s).upper().strip()
    s = s.replace("°", "")
    s = s.replace("DEGREES", "").replace("DEGREE", "")
    import re
    s = re.sub(r"\s*([CF])\b", "", s)
    for ch in "-.·_":
        s = s.replace(ch, "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main() -> int:
    # work in a temp dir so we don't pollute /app
    workdir = Path(tempfile.mkdtemp(prefix="mc3_local_"))
    corpus = workdir / "corpus"
    shutil.copytree(CORPUS, corpus)
    # ensure archive/ is empty (setup.sh would chmod the audit file too;
    # we leave that file readable in local testing because we're not root
    # and skipping chmod 000 is fine for verifying parsing correctness).
    output = workdir / "output"
    output.mkdir()
    env = os.environ.copy()
    env["MC2_OUTPUT_DIR"] = str(output)
    env["MC3_INDEX_DIR"] = str(workdir / "index")
    env["MC3_VERBOSE"] = "1"

    # Run setup.sh's effects: empty archive dir is already there; chmod audit file
    audit = corpus / "vendor" / "internal_audit.txt"
    try:
        os.chmod(audit, 0)
    except PermissionError:
        pass

    print("=== --index ===")
    r = run(["python3", str(APP), "--index", str(corpus)], env=env)
    print("stdout:", r.stdout)
    print("stderr:", r.stderr[-800:] if r.stderr else "")
    if r.returncode != 0:
        print("INDEX FAILED")
        return 1

    # Inject OCR fixtures for the two images so the image handlers can be
    # exercised end-to-end without a real OCR backend. In the container
    # the real OCR (tesseract or Florence-2) will populate these.
    import re as _re
    idx_path = workdir / "index" / "index.json"
    idx = json.loads(idx_path.read_text())
    ocr_fixtures = {
        "specs/backplane_pinout.png": (
            "TQ-40 backplane connector - pin assignment\n"
            "B11 GND\nB12 SDA\nB13 SCL\nB14 THERM_ALERT#\n"
            "B15 PRSNT#\nB16 GND\n"
            "THERM_ALERT# is asserted low when the die exceeds the warning threshold. "
            "It is the only open-drain pin in the row.\n"
            "Sheet 4 of 9 - mechanical drawings are in a separate package."
        ),
        "support/asset_label.jpg": (
            "Orrery Systems\nMODEL TQ-40\nBOARD REVISION REV-C2\n"
            "SN OS4-118823-77\nMADE IN MALAYSIA"
        ),
    }
    for r in idx["files"]:
        if r["path"] in ocr_fixtures:
            r["text"] = ocr_fixtures[r["path"]]
            r["tokens"] = [t.lower() for t in _re.findall(r"[A-Za-z0-9_]+", r["text"]) if len(t) >= 2]
            r["meta"]["ocr_done"] = True
            r["meta"]["pending_ocr"] = False
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, default=str))

    questions = json.loads(QUESTIONS.read_text())["queries"]
    correct = 0
    refused_correctly = 0
    for q in questions:
        qid = f"query_{q['n']:02d}"
        r = run(["python3", str(APP),
                 "--corpus", str(corpus),
                 "--query-id", qid,
                 "--query", q["query"]], env=env)
        out_path = output / f"{qid}_output.json"
        if r.returncode != 0 or not out_path.exists():
            print(f"\n[{qid}] FAIL: process exited {r.returncode}, no output")
            print("  stderr:", r.stderr[-400:] if r.stderr else "")
            continue
        got = json.loads(out_path.read_text())
        expected = q.get("expected_answer", "")
        exp_cites = set(q.get("expected_citations") or [])
        got_cites = set(got.get("citations") or [])
        ans_ok = normalize_answer(got.get("answer", "")) == normalize_answer(expected) if expected else got.get("answer", "") == ""
        cite_ok = got_cites == exp_cites
        ok = ans_ok and cite_ok
        if ok:
            correct += 1
        if q.get("unanswerable"):
            refused_correctly = ans_ok and cite_ok
        mark = "OK " if ok else "BAD"
        print(f"\n[{qid}] {mark}  ans={got.get('answer')!r:18s} cites={sorted(got_cites)}")
        print(f"       expected: ans={expected!r:18s} cites={sorted(exp_cites)}")
        if not ok:
            print(f"       ans_match={ans_ok}  cite_match={cite_ok}")
            if got_cites != exp_cites:
                print(f"       diff: +extra={sorted(got_cites - exp_cites)} -missing={sorted(exp_cites - got_cites)}")

    print(f"\n=== SCORE: {correct}/10 correct (refusal handled correctly: {refused_correctly}) ===")
    return 0 if correct == 10 else 2


if __name__ == "__main__":
    sys.exit(main())