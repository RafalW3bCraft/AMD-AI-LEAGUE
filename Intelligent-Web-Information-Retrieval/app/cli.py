"""Match 4 agent — CLI entrypoint.

Mirrors the Match 3 grader contract pattern (verified from multiple
prior Match 3 starter kits). Detects mode from argv:

    python3 -m app.agent --init                       # warmup (no-op for us)
    python3 -m app.agent --query-id q01 --query "..." [--start-url URL]
        -> writes /app/output/q01_output.json

Unknown flags are tolerated. If the output directory does not exist,
it is created.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from .agent import Agent, run_query
from .agent.config import load_config
from .agent.output_safety import safe_output_path, UnsafeOutputPath

log = logging.getLogger("match4.cli")


def _parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Parse CLI arguments, tolerating unknown flags.

    Returns ``(parsed, unknown)``. Unknown flags are LOGGED to stderr in
    compatibility mode so a misspelled required flag is still surfaced
    rather than silently swallowed. Required flags (--query, --query-id)
    are still enforced after parsing.
    """
    p = argparse.ArgumentParser(
        prog="match4-agent",
        description="AMD AI League Match 4 — Intelligent Web Information Retrieval",
        allow_abbrev=False,
    )
    p.add_argument("--init", action="store_true",
                   help="warmup / index phase (no-op for web variant)")
    p.add_argument("--index", help="(ignored, for Match-3 compatibility)")
    p.add_argument("--corpus", help="(ignored, for Match-3 compatibility)")
    p.add_argument("--query-id", help="query id (writes <query-id>_output.json)")
    p.add_argument("--query", help="natural-language query string")
    p.add_argument("--start-url", action="append", default=[],
                   help="optional starting URL (repeatable)")
    p.add_argument("--output-dir", help="override output directory")
    p.add_argument("--output", help="explicit output file path")
    p.add_argument("--allow-output-outside-sandbox",
                   action="store_true",
                   help=(
                       "DANGEROUS: bypass the output-path sandbox. "
                       "Requires MATCH4_ALLOW_OUTPUT_OUTSIDE_SANDBOX=1. "
                       "Use only when the grader explicitly demands a path "
                       "outside the configured roots."
                   ))
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--strict", action="store_true",
                   help="reject unknown flags (fail fast instead of compat mode)")
    return p.parse_known_args(argv)


def _is_output_sandbox_disabled() -> bool:
    """Return True iff the operator explicitly opted out of the sandbox.

    Two independent opt-ins are required, both set via env var and CLI,
    so a malicious query string cannot accidentally disable protection.
    """
    env = os.environ.get("MATCH4_ALLOW_OUTPUT_OUTSIDE_SANDBOX", "").lower() in (
        "1", "true", "yes",
    )
    return bool(env)


def _output_path(args: argparse.Namespace) -> Path:
    """Resolve the output file path under the sandbox.

    Order of precedence:
      1. ``--output`` (when explicitly provided AND the operator has
         explicitly opted out of the sandbox via env + CLI flag).
      2. The configured ``--output-dir`` if provided.
      3. ``MATCH4_OUTPUT_DIR`` from the environment.
      4. The canonical ``/app/output``.
      5. A per-user tmp dir fallback (``tempfile.mkdtemp``).
    """
    if args.output:
        if not (args.allow_output_outside_sandbox and _is_output_sandbox_disabled()):
            # The user requested a specific path but did NOT explicitly
            # opt out of the sandbox — run it through ``safe_output_path``
            # first. If it is inside an allowed root we accept; otherwise
            # we raise and the caller writes to a sandboxed default.
            try:
                return safe_output_path(
                    args.output,
                    extra_allowlist=(
                        [args.output_dir] if args.output_dir else ()
                    ),
                )
            except UnsafeOutputPath as exc:
                log.warning(
                    "explicit --output %r rejected by sandbox (%s); "
                    "falling back to default output dir",
                    args.output, exc,
                )
        else:
            # Operator explicitly opted out — accept the path verbatim,
            # but resolve symlinks and create the parent.
            p = Path(args.output).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            return p

    candidates = [
        args.output_dir,
        os.environ.get("MATCH4_OUTPUT_DIR"),
        "/app/output",
    ]
    out_dir: Path | None = None
    for cand in candidates:
        if not cand:
            continue
        p = Path(cand)
        try:
            p.mkdir(parents=True, exist_ok=True)
            out_dir = p
            break
        except (PermissionError, OSError):
            continue
    if out_dir is None:
        import tempfile
        out_dir = Path(tempfile.mkdtemp(prefix="match4-output-"))
        log.warning("no writable output dir found; using %s", out_dir)
    qid = args.query_id or "query"
    # Sanitize query id to filename-safe form.
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in qid)
    return out_dir / f"{safe}_output.json"


def _write_atomic(path: Path, data: dict) -> None:
    """Atomic write so a partial JSON never reaches the grader."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


async def _run_query_async(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.output_dir:
        cfg.output_dir = args.output_dir
    result = await run_query(
        args.query or "",
        start_urls=args.start_url or None,
        cfg=cfg,
    )
    payload = result.to_contract_json()
    out_path = _output_path(args)
    try:
        _write_atomic(out_path, payload)
        log.info("wrote %s (%d bytes)", out_path, out_path.stat().st_size)
    except Exception as exc:  # noqa: BLE001
        log.error("failed to write %s: %s", out_path, exc)
        # Even if write fails, print so the harness can capture it.
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1
    # Also print to stdout for `tee` capture.
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    args, unknown = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if unknown:
        if args.strict:
            print(
                f"match4-agent: unknown arguments: {' '.join(unknown)}",
                file=sys.stderr,
            )
            return 2
        # Compatibility mode: log unknowns but proceed.
        log.warning(
            "ignoring unknown arguments: %s (use --strict to reject)",
            " ".join(unknown),
        )
    if args.init or args.index:
        # No warmup needed for the deterministic pipeline.
        log.info("init: no warmup required for Match 4 web variant")
        return 0
    if not args.query:
        # Bare `python -m app.agent` — print usage and exit non-zero.
        _parse_args(["--help"])
        return 2
    return asyncio.run(_run_query_async(args))


if __name__ == "__main__":
    raise SystemExit(main())