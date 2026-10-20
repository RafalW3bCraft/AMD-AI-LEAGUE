"""Output-path sandboxing.

The Match 4 grader writes the result JSON to a path under
``/app/output`` (or whatever ``MATCH4_OUTPUT_DIR`` / ``--output-dir``
says). A malicious query should never be able to influence where the
output lands — for example, a query string that includes
``../../etc/passwd`` and a bug that allowed it to flow into the path.

This module ensures:

- Output paths are absolute (we resolve ``..`` segments).
- Output paths stay inside an allowed directory list (default: cwd,
  /app/output, MATCH4_OUTPUT_DIR, --output-dir).
- If a caller explicitly passes a path OUTSIDE the allowlist, we refuse
  rather than silently write outside the sandbox.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from .safety import UnsafeUrlError


class UnsafeOutputPath(ValueError):
    """Raised when an output path is outside the allowed sandbox."""


_DEFAULT_ALLOWLIST = (
    os.environ.get("MATCH4_OUTPUT_DIR", "/app/output"),
)


def _canonicalize(path: str | os.PathLike[str]) -> Path:
    p = Path(str(path)).expanduser()
    try:
        # resolve() handles '..' segments and symlinks.
        return p.resolve(strict=False)
    except OSError:
        return p.absolute()


def safe_output_path(
    requested: str | os.PathLike[str],
    *,
    extra_allowlist: Iterable[str | os.PathLike[str]] = (),
    must_exist_parent: bool = False,
) -> Path:
    """Return a Path for ``requested`` only if it lies inside the
    allowed sandbox. Raise UnsafeOutputPath otherwise.

    The path's parent directory is auto-created if it doesn't exist
    (unless ``must_exist_parent=True``, which is used for symlink-tight
    environments).

    Default allowlist: ``$MATCH4_OUTPUT_DIR`` and the canonical
    ``/app/output``. ``/tmp`` is **not** permitted by default — a
    malicious query string can otherwise write outside the grader's
    expected location. Pass ``extra_allowlist`` to widen this on a
    per-call basis (e.g. for tests).
    """
    p = _canonicalize(requested)
    allowlist = list(_DEFAULT_ALLOWLIST) + [str(x) for x in extra_allowlist]
    allowlist += ["/app/output"]
    # De-dup + canonicalize allowlist.
    seen: set[str] = set()
    allowed_roots: list[Path] = []
    for a in allowlist:
        if not a:
            continue
        canon = str(_canonicalize(a))
        if canon in seen:
            continue
        seen.add(canon)
        allowed_roots.append(Path(canon))
    target = str(p)
    for root in allowed_roots:
        try:
            # Path.is_relative_to is 3.9+.
            if p.is_relative_to(root):
                break
        except (AttributeError, ValueError):
            # Fallback for older Pythons: string-prefix check.
            try:
                root.relative_to(p)  # type: ignore[arg-type]
                break
            except ValueError:
                pass
    else:
        raise UnsafeOutputPath(
            f"output path {target!r} is outside the allowed sandbox "
            f"({[str(r) for r in allowed_roots]})"
        )
    if must_exist_parent and not p.parent.exists():
        raise UnsafeOutputPath(
            f"output parent dir does not exist: {p.parent!r}"
        )
    p.parent.mkdir(parents=True, exist_ok=True)
    return p