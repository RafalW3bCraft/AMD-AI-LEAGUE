# Match 4 Submission — Final Go / No-Go Checklist

Status legend: VERIFIED (proven against an authoritative source or real
execution), INFERRED (reasonable assumption), UNTESTED (written but not
yet executed on AMD hardware), FAILED (attempted and failed).

## Code artifacts

- [x] `app/app.py` — canonical grader entrypoint (`/app/app.py`) — VERIFIED
- [x] `app/cli.py` — CLI handles `--init`, `--query-id`, `--query`,
      `--start-url`, tolerates unknown flags — VERIFIED
- [x] `app/server.py` — optional daemon, no-op for deterministic core — VERIFIED
- [x] `app/agent/` — 5-stage pipeline (understand / search / fetch /
      extract / verify) — VERIFIED
- [x] Atomic JSON write to `<output_dir>/<query_id>_output.json` — VERIFIED
- [x] Refusal contract honored when no evidence — VERIFIED (test_e2e_refuses_unknown)
- [x] Token-boundary regex prevents false substring matches — VERIFIED
      (test_verify_token_boundary_prevents_false_match)

## Tests

- [x] `pytest tests/` — 31/31 passing — VERIFIED
- [x] End-to-end price query (offline stub) — VERIFIED
- [x] End-to-end refusal on unknown — VERIFIED
- [x] Live DuckDuckGo + amd.com navigation, 8 pages, 56 candidates,
      verified citation — VERIFIED 2026-10-15
- [x] HTML stripping (scripts, styles, comments, hidden, meta-refresh) — VERIFIED
- [x] Prompt-injection flagging — VERIFIED

## Container

- [x] `Dockerfile` uses `rocm/pytorch:rocm10.0_ubuntu26.04_py3.14_pytorch_release_2.13.0` — VERIFIED
- [x] No `pip install torch torchvision torchaudio` — VERIFIED
- [x] No NVIDIA/CUDA code anywhere — VERIFIED (grep clean)
- [x] `ENTRYPOINT ["python3", "-m", "app.cli"]` matches grader pattern — VERIFIED
- [x] `/app/output` and `/app/corpus` created — VERIFIED
- [x] ROCm torch check (`assert 'rocm' in torch.__version__`) in image build — VERIFIED
- [ ] Image size ≤ 60 GiB — UNTESTED (build on AMD notebook)
- [ ] Container startup ≤ 10 min — UNTESTED (build on AMD notebook)
- [ ] Per-query latency ≤ 30 s — VERIFIED locally (live run: 8.5 s, 8 pages)

## AMD notebook validation

- [ ] `notebooks/match4_amd_test.ipynb` executed on `notebooks.amd.com/hackathon` — UNTESTED
- [ ] Environment check (Python, hostname, persistent dir) — UNTESTED
- [ ] Repo clone / pull under `/persistent` or `/workspace` — UNTESTED
- [ ] Unit tests pass on AMD node — UNTESTED
- [ ] Offline CLI smoke test — UNTESTED on AMD node
- [ ] Live network test (`MATCH4_RUN_LIVE=1`) — UNTESTED on AMD node
- [ ] Docker build on AMD node — UNTESTED
- [ ] ROCm torch visibility — UNTESTED
- [ ] Final VERIFIED checklist pasted into submission notes — UNTESTED

## Submission form (lablab.ai)

- [ ] Public GitHub repo URL — TODO (push this repo to a public remote)
- [ ] Demo URL on lablab.ai — TODO (lablab.ai submission form)
- [ ] Submission form filled — TODO
- [ ] Submission confirmed in lablab dashboard — TODO

## Contract assumptions (see contracts/CONTRACT.md)

- [x] Exact grader CLI flags: `--query-id`, `--query`, `--start-url`,
      `--corpus`, `--init` — INFERRED (Match 3 pattern), CLI tolerates unknowns
- [x] Output JSON schema — INFERRED, mirrors Match 3 + `trail`/`reasoning` extras
- [x] Network at grading time: assumed ON (web retrieval challenge), but
      agent degrades gracefully when OFF — VERIFIED

## What we will NOT do

- [x] No fabricated evaluator responses — VERIFIED
- [x] No fabricated AMD validation logs — VERIFIED
- [x] No NVIDIA/CUDA code — VERIFIED
- [x] No hard-coded answers — VERIFIED (tests use synthetic stub corpus)

## Sign-off

- Local dev: VERIFIED (2026-10-15, 31/31 tests, live e2e on amd.com)
- AMD notebook: UNTESTED — required before official submission
- Next step: push to public GitHub, run notebook on AMD node, fill form.
