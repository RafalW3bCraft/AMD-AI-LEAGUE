"""Build a persistent evidence index over the corpus.

This is the --index pass. It walks every file defensively, parses what it can,
and writes a single JSON index under INDEX_DIR (/app/index). Per-question
processes then load this index only -- never re-parse, never re-load a model.

Defensive properties:
  * Catch per-file; never abort the walk.
  * Empty directories are fine -- they just produce no records.
  * Unreadable files (PermissionError) are skipped and logged.
  * Encrypted PDFs (pypdf raises on decrypt) are skipped.
  * Unknown file types are skipped.
  * Images are skipped at index time if no OCR backend is available;
    they get re-evaluated at query time only when the query needs vision.

The on-disk schema is:
    {
      "version": 1,
      "files": [
        {
          "path": "specs/tq40_datasheet_r2.pdf",   # corpus-relative, forward slashes
          "kind": "pdf",
          "text": "extracted full text",
          "tables": [...],                         # list of {sheet/table, rows}
          "meta": {"withdrawn": false, "sheets": ["RMA Parts", "Notes"]},
          "tokens": ["joined", "lowercase", "tokens", ...]
        },
        ...
      ]
    }
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

# --- parsers (lazy / per-format) ---------------------------------------------

def _read_text_safely(path: Path) -> str | None:
    """Read a small text file. Returns None on permission/unreadable."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (PermissionError, IsADirectoryError, OSError):
        return None


def parse_pdf(path: Path) -> dict[str, Any] | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(str(path))
        # encrypted PDFs raise on .pages access or are flagged
        if reader.is_encrypted:
            try:
                # try empty password; many "encrypted" PDFs respect this
                if reader.decrypt("") == 0:
                    return {"text": "", "tables": [], "meta": {"encrypted": True}}
            except Exception:
                return {"text": "", "tables": [], "meta": {"encrypted": True}}
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                parts.append("")
        text = "\n".join(parts)
        # filenames like *_WITHDRAWN.pdf are marked
        meta = {"withdrawn": "WITHDRAWN" in path.name.upper()}
        return {"text": text, "tables": [], "meta": meta}
    except Exception:
        # any failure (encrypted, corrupted, permission) -> skip
        return None


def parse_docx(path: Path) -> dict[str, Any] | None:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError, PermissionError, OSError):
        return None
    try:
        # paragraphs -> lines, table cells -> tab-joined
        xml = re.sub(r"</w:p>", "\n", xml)
        # inside <w:tbl>...</w:tbl>, mark rows
        xml = re.sub(r"</w:tr>", "\n", xml)
        xml = re.sub(r"</w:tc>", "\t", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        # collapse runs of blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return {"text": text, "tables": [], "meta": {}}
    except Exception:
        return None


def parse_xlsx(path: Path) -> dict[str, Any] | None:
    try:
        import openpyxl
    except ImportError:
        return None
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    except Exception:
        return None
    try:
        tables: list[dict[str, Any]] = []
        text_chunks: list[str] = []
        for sname in wb.sheetnames:
            ws = wb[sname]
            rows = []
            for row in ws.iter_rows(values_only=True):
                row_vals = [("" if c is None else str(c)) for c in row]
                if any(v.strip() for v in row_vals):
                    rows.append(row_vals)
                text_chunks.append(" | ".join(row_vals))
            tables.append({"sheet": sname, "rows": rows})
        return {
            "text": "\n".join(text_chunks),
            "tables": tables,
            "meta": {"sheets": list(wb.sheetnames)},
        }
    finally:
        try:
            wb.close()
        except Exception:
            pass


def parse_csv(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f)
            rows = [r for r in reader if r]
        text = "\n".join(",".join(r) for r in rows)
        return {"text": text, "tables": [{"sheet": "csv", "rows": rows}], "meta": {"rows": len(rows)}}
    except (PermissionError, IsADirectoryError, OSError, UnicodeDecodeError):
        return None


def parse_text_or_log(path: Path) -> dict[str, Any] | None:
    text = _read_text_safely(path)
    if text is None:
        return None
    return {"text": text, "tables": [], "meta": {}}


def parse_python(path: Path) -> dict[str, Any] | None:
    text = _read_text_safely(path)
    if text is None:
        return None
    # for python files we keep the source AND emit "name = value" lines for top-level
    # uppercase constants so the answer can pull them directly (sample Q6: 180).
    extras = []
    try:
        import ast
        tree = ast.parse(text)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                # only single-target, simple constant assignments
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    try:
                        val = ast.literal_eval(node.value)
                        extras.append(f"{name} = {val!r}")
                    except Exception:
                        pass
    except SyntaxError:
        pass
    combined = text + "\n" + "\n".join(extras)
    return {"text": combined, "tables": [], "meta": {"constants": extras}}


# --- image OCR --------------------------------------------------------------

def parse_image(path: Path) -> dict[str, Any] | None:
    """Index-time: try OCR if a model is available, else mark as pending.

    Query-time code can re-attempt OCR lazily. We don't try to keep a model
    resident here because image OCR is the slow path and only two of the
    ten questions need it.
    """
    # No OCR at index time in the default build; images get a stub.
    # The query-time answer() will lazy-load an OCR backend and process them
    # on demand, then cache results back into the index.
    return {"text": "", "tables": [], "meta": {"image": True, "pending_ocr": True}}


# --- dispatch ---------------------------------------------------------------

PARSERS = {
    ".pdf":  parse_pdf,
    ".docx": parse_docx,
    ".xlsx": parse_xlsx,
    ".csv":  parse_csv,
    ".txt":  parse_text_or_log,
    ".log":  parse_text_or_log,
    ".py":   parse_python,
    ".png":  parse_image,
    ".jpg":  parse_image,
    ".jpeg": parse_image,
}

TOKEN_RE = re.compile(r"[A-Za-z0-9_./#-]+")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text) if len(t) >= 2]


def walk_corpus(corpus: Path, on_progress=None) -> list[dict[str, Any]]:
    """Defensive recursive walk. Returns the list of file records.

    Any error per file -> record is skipped, walk continues.
    """
    records: list[dict[str, Any]] = []
    corpus = corpus.resolve()
    seen = 0
    for root, dirs, files in os.walk(corpus, followlinks=False):
        # skip hidden dirs and __MACOSX etc.
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__MACOSX")
        for name in sorted(files):
            if name.startswith("."):
                continue
            full = Path(root) / name
            try:
                rel = full.relative_to(corpus).as_posix()
            except ValueError:
                continue
            ext = full.suffix.lower()
            parser = PARSERS.get(ext)
            seen += 1
            if parser is None:
                # unknown type -- skip silently
                if on_progress:
                    on_progress(seen, rel, "skipped-unknown")
                continue
            try:
                parsed = parser(full)
            except Exception as e:
                parsed = None
                if on_progress:
                    on_progress(seen, rel, f"error:{type(e).__name__}")
            if parsed is None:
                if on_progress:
                    on_progress(seen, rel, "skipped-unreadable")
                continue
            text = parsed.get("text") or ""
            record = {
                "path": rel,
                "kind": ext.lstrip("."),
                "text": text,
                "tables": parsed.get("tables", []),
                "meta": parsed.get("meta", {}),
                "tokens": tokenize(text),
                "size": full.stat().st_size,
            }
            records.append(record)
            if on_progress:
                on_progress(seen, rel, "ok")
    return records


def build_index(corpus: Path, index_dir: Path, on_progress=None) -> Path:
    index_dir.mkdir(parents=True, exist_ok=True)
    records = walk_corpus(corpus, on_progress=on_progress)
    payload = {
        "version": 1,
        "corpus_root": str(corpus),
        "file_count": len(records),
        "files": records,
    }
    out = index_dir / "index.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(out)
    return out


# --- query-time helpers -----------------------------------------------------

def load_index(index_dir: Path) -> dict[str, Any]:
    p = Path(index_dir) / "index.json"
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_index(index_dir: Path, payload: dict[str, Any]) -> None:
    p = Path(index_dir) / "index.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(p)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", type=Path)
    ap.add_argument("--out", type=Path, default=Path("/tmp/mc3_index"))
    args = ap.parse_args()

    def prog(n, p, s):
        print(f"[{n}] {s}: {p}", file=sys.stderr)

    out = build_index(args.corpus, args.out, on_progress=prog)
    print(f"wrote {out}", file=sys.stderr)