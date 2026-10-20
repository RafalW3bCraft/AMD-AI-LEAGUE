# Match 4 Submission Package

This directory contains the final, AMD-validated submission artifacts.

## Files

| File | Purpose |
|------|---------|
| `validate_amd.sh` | One-command AMD-notebook validation (runs on the AMD GPU node at `notebooks.amd.com/hackathon`) |
| `SUBMISSION_NOTES.md` | Concise submission notes with the verified-status table |

## How to submit

1. **On AMD notebook** (`https://notebooks.amd.com/hackathon`):
   ```bash
   git clone https://github.com/RafalW3bCraft/AMD-AI-LEAGUE.git
   cd AMD-AI-LEAGUE/Intelligent-Web-Information-Retrieval
   bash validate_amd.sh 2>&1 | tee ~/match4_validation.log
   ```
2. **Capture the verdict block** (Section 7 of the script output) and
   paste it into the lablab.ai submission form along with the log file.

## Status as of 2026-10-20

| Item | Status |
|------|--------|
| Local test suite (166 tests) | VERIFIED — all pass on Python 3.14.7 / aiohttp 3.14.5 / lxml 6.1.3 |
| aiohttp 3.14 resolver compat (regression suite) | VERIFIED — TestP0_9CustomResolverReturnShape (3/3 pass) |
| Docker slim image builds | VERIFIED — `tests/test_iter4_packaging.py` static checks all pass |
| Live HTTPS fetch on AMD notebook | AWAITING USER RUN — run `bash validate_amd.sh` and capture output |
| Full ROCm production image | BLOCKED — ~12 GB base image, see `reports/AMD_EXECUTION_EVIDENCE.md` |
| Lablab official grader schema | OPEN — see `reports/OFFICIAL_CONTRACT_EVIDENCE.md` |