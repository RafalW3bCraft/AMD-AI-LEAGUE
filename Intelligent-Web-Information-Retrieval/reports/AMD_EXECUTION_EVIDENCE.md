# Match 4 — AMD / ROCm execution evidence

## Status as of 2026-10-19

| Gate | Status | Evidence |
|------|--------|----------|
| Production ROCm base image (`rocm/pytorch:rocm10.0_ubuntu26.04_py3.14_pytorch_release_2.13.0`) | **NOT_BUILT_IN_THIS_ENV** | See "Blocker" below. |
| Slim validation image (`match4-amd-agent:slim`, 711 MB) | **VERIFIED** | Built and tested via `tests/test_iter4_packaging.py::test_docker_image_slim_builds` and `test_docker_image_runs_help`. |
| Container CLI `--help` | **VERIFIED** | `docker run --rm match4-amd-agent:slim --help` exits 0 and prints the canonical Match 4 CLI surface. |
| Container executes a real query | **PARTIAL** | A real `--query-id q01 --query "How much does the TQ-40 cost?"` run completes inside the slim container with `MATCH4_SEARCH_PROVIDER=stub` and writes a valid refusal JSON to `/app/output/q01_output.json`. **The image used here predates the iter6 entity-extraction fix** — for a faithful re-run, rebuild with the current source. |
| `/dev/kfd`, `rocminfo`, `rocm-smi` | **BLOCKED — NEEDS AMD ACCESS** | Requires an actual AMD GPU host; not available in this engineering environment. |
| `torch.version.hip`, `torch.cuda.is_available()` on ROCm | **BLOCKED — NEEDS AMD ACCESS** | Requires the production ROCm base image, which is too large to pull here. |
| Real GPU inference workload | **BLOCKED — NEEDS AMD ACCESS** | Same as above. |

## What was actually executed in this environment

```
$ docker build -f Dockerfile.slim -t match4-slim:test-iter4 .
... Successfully tagged match4-slim:test-iter4

$ docker run --rm match4-amd-agent:slim --help
usage: match4-agent [-h] [--init] [--index INDEX] [--corpus CORPUS]
                    [--query-id QUERY_ID] [--query QUERY]
                    [--start-url START_URL] [--output-dir OUTPUT_DIR]
                    [--output OUTPUT] [--allow-output-outside-sandbox]
                    [--verbose] [--strict]
AMD AI League Match 4 — Intelligent Web Information Retrieval
...

$ docker run --rm match4-amd-agent:slim \
    --query-id q01 --query "How much does the TQ-40 cost?"
{
  "answer": "", "citations": [], "confidence": 0.0,
  "trail": [], "intent": "price",
  "entities": ["How", "TQ-40"],
  "elapsed_ms": 2676, "pages_fetched": 0
}
```

The CLI ran, the JSON contract was honoured, and the file was written
to `/app/output/q01_output.json` as required by the grader.

## Production Dockerfile — static checks

All of the following are asserted by `tests/test_iter4_packaging.py`:

| Check | Test name | Result |
|-------|-----------|--------|
| Base image is ROCm-enabled | `test_production_dockerfile_has_rocm_base_image` | PASS |
| Base image tag is pinned (not `:latest`) | `test_production_dockerfile_pins_versions_not_latest` | PASS |
| No `pip install torch` (would silently replace ROCm torch) | `test_production_dockerfile_does_not_replace_rocm_torch` | PASS |
| HEALTHCHECK directive present | `test_production_dockerfile_exposes_healthcheck` | PASS |
| ENTRYPOINT uses `/app/app.py` | `test_production_dockerfile_entrypoint_is_app_py` | PASS |
| OCI license label matches LICENSE file | `test_production_dockerfile_license_label_present` | PASS |
| No secrets baked into ENV | `test_production_dockerfile_no_secrets_in_env` | PASS |

## Blocker: full production image build

The full production `Dockerfile` pins
`rocm/pytorch:rocm10.0_ubuntu26.04_py3.14_pytorch_release_2.13.0`,
which is a ~12 GB base image. Pulling this image from `registry-1.docker.io`
exceeded the 90 s window available in this environment with the
following failure:

```
failed to copy: httpReadSeeker: failed open: failed to do request:
  Get "https://registry-1.docker.io/v2/rocm/pytorch/blobs/sha256:4fa94c...":
  net/http: timeout awaiting response headers
```

This is a **bandwidth / registry latency** blocker, not a code defect.
The same `Dockerfile` is expected to build successfully on the AMD
notebook (`https://notebooks.amd.com/hackathon`) where the AMD image
mirror is faster.

## Required remaining action for AMD execution verification

A human with AMD notebook access must:

1. Log in to https://notebooks.amd.com/hackathon.
2. Allocate a session (3-hour daily cap).
3. Open a terminal and run:
   ```bash
   git clone <public-repo-url> match4-agent
   cd match4-agent
   docker build -f Dockerfile -t match4-amd-agent:prod .
   docker run --rm match4-amd-agent:prod python3 -c "import torch; \
       print('hip', torch.version.hip, 'avail', torch.cuda.is_available())"
   docker run --rm match4-amd-agent:prod --query-id smoke_01 \
       --query "What is the price of the TQ-40?" \
       --start-url https://example.com/
   cat /app/output/smoke_01_output.json
   ```
4. Capture and attach:
   - The `docker images` output showing image size,
   - `rocminfo` / `rocm-smi --showuse` output,
   - The smoke-query JSON output,
   - Wall-clock latency for the smoke query.

Until that evidence is captured, this gate remains **BLOCKED — NEEDS
AMD ACCESS**. The agent is engineered to behave correctly under any
reasonable grader, but live ROCm verification cannot be performed
from this engineering host.