"""Canonical AMD grader entrypoint: `/app/app.py`.

This file is the grader's expected path. It must work whether invoked as:

    python3 /app/app.py --help                     # direct script
    python3 -m app.cli --help                       # module path
    python3 /app/app.py --query-id q1 --query ...   # grader-style

The Match 4 grader contract (from the lablab.ai Match 4 brief plus the
Match 3 starter-kit pattern — see ``contracts/CONTRACT.md``) is:

    python3 /app/app.py --index /app/corpus         # warmup (no-op for web)
    python3 /app/app.py --corpus /app/corpus --query-id query_01 \\
        --query "What is the price of the TQ-40?"
    -> /app/output/query_01_output.json

Direct-script execution is the most common way the grader invokes the
entrypoint. The previous implementation did ``from app.cli import main``
with no ``sys.path`` bootstrap, which failed when ``/app`` was not on the
Python path (the very condition the grader produces when it runs the
script by absolute path). This version:

1. Ensures the directory containing ``app/`` is on ``sys.path`` so the
   top-level ``app`` package is importable.
2. Performs the same dance when run as a module (``python3 -m app.cli``).
3. Provides a clean ``SystemExit(main())`` so the grader captures our
   process exit code exactly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_sys_path() -> None:
    """Make ``app`` importable regardless of how this file is invoked.

    Two cases to handle:
      (a) The file lives inside a directory whose parent holds the top-level
          ``app`` package (the typical /app layout: /app/app.py + /app/app/...).
          We must add the file's parent directory itself, not its parent.
      (b) Someone runs ``python /some/path/app/app.py`` from elsewhere.
          Then ``app/`` is the directory containing this file, and the
          package root is the parent of ``app/`` (i.e. the user's repo root).

    The bootstrap adds whichever path makes ``import app.cli`` succeed.
    """
    here = Path(__file__).resolve().parent  # the directory holding app.py
    # Layout (a): /app/app.py and /app/app/...  — `here` is the package root.
    if (here / "app" / "__init__.py").is_file() and (here / "app" / "cli.py").is_file():
        candidates = [str(here)]  # `here` is the parent of the `app` package
    else:
        # Layout (b): file is inside `app/` (i.e. `app/app.py`) — go up one.
        candidates = [str(here.parent), str(here)]

    for cand in candidates:
        if cand and cand not in sys.path:
            sys.path.insert(0, cand)

    # Final guard: if import still fails, surface a clearer error than the
    # bare ``ModuleNotFoundError`` we previously emitted.
    try:
        import app.cli  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "match4 entrypoint: cannot import app.cli from "
            f"{os.getcwd()!r}; tried sys.path={sys.path[:5]}… ({exc})"
        ) from exc


_bootstrap_sys_path()

from app.cli import main  # noqa: E402  (import after sys.path bootstrap)

if __name__ == "__main__":
    raise SystemExit(main())