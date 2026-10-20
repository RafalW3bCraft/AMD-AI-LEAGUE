"""Packaging / grader-replay tests (Iteration 4).

These tests verify the deployment surface (entrypoints, output schema,
Dockerfile path resolution) without requiring Docker to be available.
The companion `reports/iter4_docker.md` records the actual Docker
build / run output from the audit host.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PY = REPO_ROOT / "app" / "app.py"


def _app_py() -> str:
    """Path to app.py, preferring the repo location but falling back to
    the /app/app.py copy inside the slim container."""
    # If we're inside the slim container, app.py lives at /app/app.py.
    if os.path.exists("/app/app.py"):
        return "/app/app.py"
    return str(REPO_ROOT / "app" / "app.py")


def _repo_root() -> Path:
    """Return the actual repo root, whether local or in-container.

    Inside the slim container the tests run from /app (the workdir);
    we detect this and use a fallback Dockerfile lookup path."""
    if (REPO_ROOT / "Dockerfile").is_file():
        return REPO_ROOT
    if Path("/app/Dockerfile").is_file():
        return Path("/app")
    return REPO_ROOT


# --------------------- entrypoint invocations ---------------------------

def test_app_py_help():
    """`python3 app/app.py --help` works from the repo root."""
    r = subprocess.run(
        [sys.executable, _app_py(), "--help"],
        capture_output=True, text=True, cwd=str(_repo_root()),
    )
    assert r.returncode == 0, r.stderr
    assert "match4-agent" in r.stdout


def test_app_py_help_from_tmp(tmp_path):
    """`python3 app/app.py --help` works from any CWD."""
    r = subprocess.run(
        [sys.executable, _app_py(), "--help"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    assert "match4-agent" in r.stdout


def test_module_help():
    """`python -m app.cli --help` works."""
    r = subprocess.run(
        [sys.executable, "-m", "app.cli", "--help"],
        capture_output=True, text=True, cwd=str(_repo_root()),
    )
    assert r.returncode == 0, r.stderr


# --------------------- grader-style invocation --------------------------

def test_grader_invocation_writes_valid_json(tmp_path):
    """Replicate the AMD grader pattern: per-query fresh process,
    output JSON to MATCH4_OUTPUT_DIR."""
    env = os.environ.copy()
    env["MATCH4_SEARCH_PROVIDER"] = "stub"
    env["MATCH4_OUTPUT_DIR"] = str(tmp_path)
    r = subprocess.run(
        [sys.executable, _app_py(),
         "--query-id", "iter4_grader",
         "--query", "How much does the TQ-40 cost?"],
        capture_output=True, text=True, cwd=str(_repo_root()), env=env,
    )
    assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"
    out_file = tmp_path / "iter4_grader_output.json"
    assert out_file.is_file(), f"missing output: {list(tmp_path.iterdir())}"
    data = json.loads(out_file.read_text(encoding="utf-8"))
    # Required contract fields per contracts/CONTRACT.md.
    required = {"answer", "citations", "confidence"}
    assert required.issubset(data.keys()), \
        f"missing required fields: {required - set(data.keys())}"
    # Schema-correct types.
    assert isinstance(data["answer"], str)
    assert isinstance(data["citations"], list)
    assert isinstance(data["confidence"], (int, float))


def test_grader_init_returns_zero():
    """The grader-style `--init` (warmup) returns 0 without doing work."""
    r = subprocess.run(
        [sys.executable, _app_py(), "--init"],
        capture_output=True, text=True, cwd=str(_repo_root()),
    )
    assert r.returncode == 0, r.stderr


# --------------------- Dockerfile / container ---------------------------

def _dockerfile() -> Path:
    repo = _repo_root()
    if (repo / "Dockerfile").is_file():
        return repo / "Dockerfile"
    if Path("/app/Dockerfile").is_file():
        return Path("/app/Dockerfile")
    raise FileNotFoundError("Dockerfile not found in repo or /app")


def test_dockerfile_exists():
    """The Match 4 Dockerfile is at the repo root."""
    assert _dockerfile().is_file()


def test_dockerfile_uses_canonical_app_py():
    """The Dockerfile entrypoint invokes /app/app.py."""
    text = _dockerfile().read_text(encoding="utf-8")
    assert "/app/app.py" in text


def test_dockerfile_verifies_rocm_torch_attribute():
    """The Dockerfile checks torch.version.hip, not a version substring."""
    text = _dockerfile().read_text(encoding="utf-8")
    assert "torch.version.hip" in text


def test_dockerfile_creates_output_corpus_dirs():
    text = _dockerfile().read_text(encoding="utf-8")
    assert "/app/output" in text
    assert "/app/corpus" in text


def _in_container() -> bool:
    """True when running inside a container (Docker or otherwise)."""
    return os.path.exists("/.dockerenv") or os.path.exists("/app/Dockerfile")


def _docker_available() -> bool:
    """True iff a working Docker daemon is reachable."""
    if not os.path.exists("/usr/sbin/docker") and \
            not os.path.exists("/usr/bin/docker"):
        return False
    try:
        r = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


_DOCKER_OK = _docker_available()


@pytest.mark.skipif(not _DOCKER_OK,
                    reason="docker not available or non-functional")
@pytest.mark.skipif(_in_container(),
                    reason="docker-in-docker not supported in slim container")
def test_docker_image_slim_builds():
    """The slim validation image builds successfully.

    Uses ``--target`` slicing + cache from any prior build so this
    completes in well under 60 s on a warm Docker cache. On a cold
    cache the build can take ~2–3 minutes.
    """
    r = subprocess.run(
        ["docker", "build", "-f", "Dockerfile.slim",
         "-t", "match4-amd-agent:test-iter4", ".",
         "--build-arg", "BUILDKIT_INLINE_CACHE=1"],
        capture_output=True, text=True, cwd=str(_repo_root()),
        timeout=900,
    )
    assert r.returncode == 0, f"stderr: {r.stderr[-2000:]}"
    assert "Successfully tagged match4-amd-agent:test-iter4" in r.stdout


@pytest.mark.skipif(not _DOCKER_OK,
                    reason="docker not available or non-functional")
@pytest.mark.skipif(_in_container(),
                    reason="docker-in-docker not supported in slim container")
def test_docker_image_runs_help():
    """The slim validation image runs --help successfully."""
    r = subprocess.run(
        ["docker", "run", "--rm", "match4-amd-agent:test-iter4", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout[:500]}"
    assert "match4-agent" in r.stdout


# --------------------- Iter 6: production Dockerfile static checks -----


def test_production_dockerfile_has_rocm_base_image():
    """The production Dockerfile MUST pin a ROCm-enabled base image
    (so AMD GPU access works in the grader). We assert the base image
    mentions ROCm — the exact tag is INFERRED from Match 3 starter
    kits and may change once the official Match 4 kit is verified.
    """
    text = _dockerfile().read_text(encoding="utf-8")
    first_from = ""
    for line in text.splitlines():
        if line.strip().startswith("FROM "):
            first_from = line.strip()
            break
    assert first_from, "Dockerfile has no FROM directive"
    assert "rocm" in first_from.lower(), (
        f"Production Dockerfile base image is not ROCm: {first_from!r}"
    )


def test_production_dockerfile_pins_versions_not_latest():
    """The base image tag must be pinned (no :latest, no floating tags)
    so the build is reproducible across grader sessions."""
    text = _dockerfile().read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("FROM "):
            tag = line.strip().split()[-1]
            assert ":" in tag, (
                f"FROM tag has no version pin: {line!r}"
            )
            base, _, version = tag.partition(":")
            assert version and "latest" not in version.lower(), (
                f"FROM tag uses :latest or floating tag: {tag!r}"
            )


def test_production_dockerfile_does_not_replace_rocm_torch():
    """Defensive: we never ``pip install torch`` in the production
    Dockerfile because the base image already ships the ROCm build;
    pip would silently replace it with CUDA."""
    text = _dockerfile().read_text(encoding="utf-8")
    for line in text.splitlines():
        # Skip pure-comment lines.
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        low = line.lower()
        if "pip install" in low and (
            "torch" in low or "torchvision" in low or "torchaudio" in low
        ):
            assert False, (
                f"Dockerfile installs torch/torchvision/torchaudio: {line!r}"
            )


def test_production_dockerfile_exposes_healthcheck():
    """The production image must define a HEALTHCHECK so the grader
    can detect a broken entrypoint without manual intervention."""
    text = _dockerfile().read_text(encoding="utf-8")
    assert "HEALTHCHECK" in text, (
        "Dockerfile has no HEALTHCHECK directive"
    )


def test_production_dockerfile_entrypoint_is_app_py():
    """The ENTRYPOINT must be the canonical /app/app.py wrapper
    (not ``python -m app.cli`` directly, not a shell)."""
    text = _dockerfile().read_text(encoding="utf-8")
    has_entrypoint_app_py = False
    for line in text.splitlines():
        if line.strip().startswith("ENTRYPOINT"):
            assert "/app/app.py" in line, (
                f"ENTRYPOINT does not use /app/app.py: {line!r}"
            )
            has_entrypoint_app_py = True
    assert has_entrypoint_app_py, "Dockerfile has no ENTRYPOINT"


def test_production_dockerfile_license_label_present():
    """The OCI image license label must match the LICENSE file."""
    text = _dockerfile().read_text(encoding="utf-8")
    assert "org.opencontainers.image.licenses" in text, (
        "Dockerfile missing OCI license label"
    )
    repo = _repo_root()
    if (repo / "LICENSE").is_file():
        lic_text = (repo / "LICENSE").read_text(
            encoding="utf-8", errors="ignore",
        )
        if "Apache" in lic_text:
            assert "apache" in text.lower() or "Apache-2.0" in text


def test_production_dockerfile_no_secrets_in_env():
    """No API keys, tokens, or passwords should be baked into the
    Dockerfile ENV layer."""
    text = _dockerfile().read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("ENV"):
            low = line.lower()
            for secret_word in (
                "api_key", "apikey", "secret", "password",
                "token", "private_key",
            ):
                # Allow MATCH4_* configuration knobs.
                if secret_word in low and "match4" not in low:
                    assert False, (
                        f"Dockerfile ENV contains secret-like word "
                        f"{secret_word!r}: {line!r}"
                    )


# --------------------- AMD execution status --------------------------------

def test_amd_execution_evidence_note():
    """Stub for the AMD execution evidence note. This is a documentation
    marker — the actual /app/AMD_EXECUTION.md file is generated by the
    AMD runbook step in Cycle 3 of the finalization prompt."""
    note = _repo_root() / "reports" / "AMD_EXECUTION_EVIDENCE.md"
    # We don't require the note to exist at pytest time — it is
    # generated post-Cycle-3 by the runbook. Just verify the
    # directory exists.
    assert (_repo_root() / "reports").is_dir()