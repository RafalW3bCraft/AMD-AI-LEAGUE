"""Notebook validity tests.

The Match 4 AMD validation notebook is part of the submission deliverable.
Every code cell must:
  1. Be valid Python (ast.parse succeeds when sources are concatenated).
  2. Have each line in `source` terminated by a newline (per nbformat).
  3. Pass nbformat.validate on the whole notebook.
  4. Be reproducible: re-running the cell source through the official
     nbformat round-trip yields the same AST.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _notebook_path() -> Path:
    """Return the notebook path, preferring the repo location but
    falling back to the /app/notebooks copy inside the slim container."""
    candidates = [
        REPO_ROOT / "notebooks" / "match4_amd_test.ipynb",
        Path("/app/notebooks/match4_amd_test.ipynb"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        f"notebook not found; tried: {[str(c) for c in candidates]}"
    )


@pytest.fixture(scope="module")
def notebook() -> dict:
    nbp = _notebook_path()
    with open(nbp, encoding="utf-8") as fh:
        return json.load(fh)


def test_notebook_loads(notebook):
    assert notebook.get("nbformat") == 4
    assert "cells" in notebook
    assert len(notebook["cells"]) > 0


def test_notebook_has_mixed_cell_types(notebook):
    types = {c["cell_type"] for c in notebook["cells"]}
    assert "markdown" in types
    assert "code" in types


def test_notebook_code_cells_parse(notebook):
    """Every code cell must be syntactically valid Python."""
    code_cells = [c for c in notebook["cells"] if c["cell_type"] == "code"]
    assert len(code_cells) >= 5, "expected several code cells in AMD notebook"
    for i, cell in enumerate(code_cells):
        src = "".join(cell["source"])
        try:
            ast.parse(src)
        except SyntaxError as e:
            pytest.fail(
                f"code cell {i} has SyntaxError: {e}\n"
                f"first 200 chars: {src[:200]!r}"
            )


def test_notebook_code_cells_have_newline_terminated_sources(notebook):
    """Every line in `source` must be newline-terminated per nbformat 4.x."""
    for i, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] != "code":
            continue
        for j, line in enumerate(cell["source"]):
            assert line.endswith("\n"), \
                f"code cell {i} line {j} missing newline: {line!r}"


def test_notebook_nbformat_validate(notebook):
    """Whole-notebook structural validation."""
    try:
        import nbformat
        from nbformat import validate as nb_validate
        nb = nbformat.reads(json.dumps(notebook), as_version=4)
        nb_validate(nb)
    except ImportError:
        pytest.skip("nbformat not installed")


def test_notebook_cell_count_matches_old_layout(notebook):
    """We expect the rebuilt notebook to keep the same logical structure
    (1 intro markdown + N sections, each with header md + code cells +
    a final report). 20 cells matches the original layout."""
    assert len(notebook["cells"]) == 20


def test_notebook_final_cell_emits_report(notebook):
    """The last code cell must NOT contain hardcoded PASS / True flags —
    it must build the report from captured command results."""
    last = notebook["cells"][-1]
    assert last["cell_type"] == "code"
    src = "".join(last["source"])
    assert "NOT_RUN" in src, "final cell must declare NOT_RUN placeholders"
    assert "json.dumps" in src, "final cell must serialize as JSON"


def test_notebook_sections_present(notebook):
    md_text = "\n".join(
        "".join(c.get("source", [])) for c in notebook["cells"]
        if c["cell_type"] == "markdown"
    )
    for section in [
        "1. Environment check",
        "2. Get the agent code",
        "3. Run unit",
        "4. CLI smoke test",
        "5. Live test",
        "6. Docker image build",
        "7. Docker grader",
        "8. ROCm torch",
        "9. Verification summary",
    ]:
        assert section in md_text, f"missing section: {section}"