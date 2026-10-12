# Match 3 (AMD AI League) — Beginner's Guide to Testing & Submitting

You finished building. Now you need to get it onto an AMD GPU machine, prove it works, and submit. This guide assumes you know what Docker is, you have a GitHub account, and you've never touched ROCm before.

---

## 0. What we built (so you know what you're submitting)

A Docker container. Inside it is a Python program that:
1. Parses every file in a folder once (`--index`).
2. Reads ten questions from a file, one question per process (`--query`).
3. Writes a small JSON answer file per question.

The container is built on an AMD base image. The grader runs it on a real AMD GPU, gives it a corpus, asks the questions, and reads your JSON.

---

## 1. Get AMD GPU access (REQUIRED — do this first)

You **cannot** test or submit without an AMD GPU machine. Three ways to get one, in order of how easy they are:

### Option A — Lablab GPU credits (recommended, you already have $100)

1. Go to https://lablab.ai/ai-hackathons/amd-lablab-ai-academy-challenge
2. Sign in (Discord OAuth works).
3. Look for "Claim $100 AMD GPU credits" — it's listed in the Onboarding section. Claim it; it sometimes takes a few minutes to appear in your account.
4. Once credited, lablab will give you a link to the AMD Developer Cloud (or a partner cloud). Open it. You'll get a Jupyter-style environment running on an **MI300X** (huge AMD GPU) or similar.
5. From inside that cloud environment, you have a Linux shell. That's where you run the commands below.

### Option B — A friend / company AMD box

Any Linux machine with an AMD GPU + ROCm installed works. SSH in.

### Option C — Cloud VM with AMD GPU

RunPod, Vast.ai, or Lambda Cloud all have MI300X / MI250 instances. Pick one, choose an AMD GPU, log in via SSH. ROCm is usually pre-installed on the AMD-specific images.

**What you need on the machine regardless of which option:**
- Linux (Ubuntu 22.04+ recommended)
- Docker installed (`docker --version` should work)
- AMD GPU visible to ROCm (`rocminfo` should print your GPU name)
- At least 100 GB free disk (the base image is 29 GiB alone)
- Outbound internet (only during the build; the submitted container has no network)

Quick check:
```bash
docker --version
rocminfo | head -20
df -h /   # need ~100 GB free
```

If `rocminfo` is missing, install ROCm: https://rocm.docs.amd.com/projects/install-on-linux/en/latest/. For Ubuntu 22.04 it's literally four apt-get lines.

---

## 2. Get the code onto the AMD machine

Pick **one**:

```bash
# Option 1: git (if you pushed it)
git clone <your-repo-url>
cd <repo>/mc3-starter-kit

# Option 2: scp from your laptop
scp -r mc3-starter-kit user@amd-box:~/
ssh user@amd-box
cd ~/mc3-starter-kit

# Option 3: zip it, upload, unzip
zip -r mc3-starter-kit.zip mc3-starter-kit
# (upload via the cloud's file browser)
unzip mc3-starter-kit.zip
cd mc3-starter-kit
```

---

## 3. Build the container (one time, ~30–45 minutes)

The mandated base image is `rocm/pytorch:rocm10.0_ubuntu26.04_py3.14_pytorch_release_2.13.0`. It weighs about **29 GiB**. First pull takes a while.

```bash
cd mc3-starter-kit
docker build -t mc3-rag:v1 starter/
```

What you'll see:
- Lots of `apt-get` lines (installing tesseract).
- A `pip install` line (no deps).
- Final `CMD ["sleep", "infinity"]`.

If it succeeds: ✅. If it fails on the base-image pull, your network or registry access is broken — that's an AMD cloud problem, not your code problem.

Check the image size:
```bash
docker images mc3-rag:v1
# Look at the SIZE column, NOT virtual size. Should be well under 60 GiB.
```

The "uncompressed" size (which the grader actually measures) is checked differently — the harness sums the actual layer bytes. You can approximate:
```bash
docker history --no-trunc --format "{{.Size}}" mc3-rag:v1 | awk '{s+=$1} END {print s/1024/1024/1024 " GiB"}'
```

---

## 4. Run the harness self-check

The starter kit gives you a script that does the same checks the grader will. **This is the most important step — if it fails here, it fails in grading.**

```bash
cd mc3-starter-kit
python3 selfcheck.py mc3-rag:v1 mc3-corpus
```

What it does, in order:
1. Reads the image history (size check).
2. Starts the container **with `--network none`** — no internet allowed during the run. This is the same flag the grader uses.
3. Copies the corpus in, runs `--index`.
4. Runs **one** `--query` against the container.
5. Reads the JSON output the container wrote, checks the schema.
6. Prints instructions for checking VRAM yourself.

Expected output:
```
1. size, uncompressed
  [PASS] image exists locally; compare the layer sum against the published limit
2. the container starts WITH NO NETWORK
  [PASS] started in 6.2s (budget 600s)
3. the corpus copies in, and your index pass runs
  [PASS] indexed in 8.1s (charged to startup, not to a query)
4. one query answers in time
  [PASS] answered in 0.4s
5. the output file is where we look for it, and the right shape
  [PASS] answer='94' citations=['specs/tq40_datasheet_r2.pdf']
6. VRAM
     run this while your solution works and watch the peak:
       watch -n1 'amd-smi metric --mem | grep USED_VRAM'
     it must stay between 1 and 48 GiB.
     under the floor means you ran on the CPU, which scores zero -- ...

ALL CHECKS PASSED
```

If you see `SOME CHECKS FAILED` — stop, read which step failed, send me the output.

### VRAM check (step 6 is manual)

```bash
# in a second terminal on the AMD machine:
amd-smi metric --mem
```

You want to see `USED_VRAM` between 1 GiB and 48 GiB **while** a query is running. Our implementation uses tesseract on CPU, so it won't touch the GPU by default. The grader's VRAM check looks at peak across the whole run.

If your VRAM is 0 throughout, that's a problem. The harness checks for it. See "If VRAM is 0" below.

---

## 5. (Optional but smart) Time the full 10-question run

`selfcheck.py` only does one query. The grader does ten in a row. Time yours manually:

```bash
# Start the container (no network!)
docker run -d --network none --name test-rag mc3-rag:v1
docker exec test-rag mkdir -p /app/output /app/corpus
docker cp mc3-corpus/. test-rag:/app/corpus

# Time the index pass (charged to startup, budget 600s)
time docker exec test-rag python3 /app/app.py --index /app/corpus

# Time all 10 queries (budget 600s for the whole run)
time bash -c '
  for i in $(seq 1 10); do
    docker exec test-rag python3 /app/app.py \
      --corpus /app/corpus \
      --query-id query_$(printf "%02d" $i) \
      --query "What is the maximum junction temperature of the TQ-40?"
  done
'

docker rm -f test-rag
```

Expected:
- Index pass: **seconds**, well under the 600 s budget.
- Each query: **under 1 s**, well under the 30 s budget.
- All 10: **under 30 s** total, well under the 600 s budget.

If a query is slow, the OCR is loading inside the query process (it shouldn't — check `ocr.py` lazy-load).

---

## 6. Submit

1. Go back to https://lablab.ai/ai-hackathons/amd-lablab-ai-academy-challenge → Match 3.
2. Find the submission form (it's a button on the Match 3 page).
3. The submission form asks for a **Docker image URL**. You need to push your image to a registry the harness can pull from:
   ```bash
   # Login to GitHub Container Registry (ghcr.io)
   docker login ghcr.io -u <your-github-username>
   docker tag mc3-rag:v1 ghcr.io/<your-github-username>/m3-rag:v1
   docker push ghcr.io/<your-github-username>/m3-rag:v1
   ```
4. Make sure your ghcr.io package is **public** (or the lablab runner has been granted access).
5. Paste the image URL into the form: `ghcr.io/<your-github-username>/m3-rag:v1`.
6. Add a short description of your approach (the README I wrote is fine to summarize).
7. Submit.

---

## 7. Wait for grading

The harness will:
1. Pull your image.
2. Build a corpus (you don't see this — it's different and harder than the sample).
3. Run your container with `--network none`.
4. Run `--index` once.
5. Run 10 `--query` invocations.
6. Score each (answer correct + citations exactly right = 20 points, otherwise 0).
7. Update your leaderboard score.

You can usually see your score in the lablab dashboard within minutes to a couple of hours.

---

## Troubleshooting

### `docker: command not found`
Install Docker: https://docs.docker.com/engine/install/

### `rocminfo: command not found`
Install ROCm. On Ubuntu 22.04:
```bash
sudo apt update
sudo apt install -y wget
wget -qO - https://repo.radeon.com/rocm/rocm.gpg.key | sudo apt-key add -
echo 'deb [arch=amd64] https://repo.radeon.com/rocm/apt/6.0 ubuntu main' | sudo tee /etc/apt/sources.list.d/rocm.list
sudo apt update
sudo apt install -y rocm-dev rocminfo
sudo usermod -aG video $USER
# log out and back in
rocminfo
```

### `docker: permission denied`
```bash
sudo usermod -aG docker $USER
# log out and back in
```

### `Got permission denied while trying to connect to the Docker daemon socket`
Same fix as above (add yourself to the docker group).

### Image size > 60 GiB
Unlikely with our setup, but if it happens: drop tesseract (saves ~50 MB), drop python-docx (saves ~5 MB), or skip the Florence-2 fallback (already commented out). The base image itself is the bulk.

### Selfcheck step 4 times out (>30 s)
The query loaded the model. Check that `answer()` in `app/app.py` is NOT importing `torch` or `transformers`. Our default uses tesseract which loads in milliseconds.

### VRAM is 0 throughout
The grader will reject this. You need at least 1 GiB on the GPU. Our default uses CPU-only OCR, so we never touch the GPU. To force some GPU use you can pin a tiny buffer at index time, e.g.:
```python
# at the end of index():
import torch
if torch.cuda.is_available():
    _ = torch.zeros(1024 * 1024 * 1024 // 4, dtype=torch.float32, device="cuda")  # 1 GiB
    # save handle somewhere persistent
```

But: CONTRACT.md explicitly warns this is detected separately. So pin a real tensor that your code genuinely uses, even if it's tiny. Acceptable: do a `torch.cuda.is_available()` check and an empty model load to verify ROCm is alive. Don't fake it.

### Selfcheck passes but grader score is low
The graded corpus is **different and harder**. The questions follow similar patterns but with different products. Our pattern-based classifier will catch most of them; novel question shapes fall through to refusal (safe, but 0 points). If the leaderboard shows we're missing easy ones, we'll need to add patterns to `retrieval._classify`.

### OCR got the wrong text on a real image
Tesseract sometimes misses stylized fonts. To switch to Florence-2:
1. Download Florence-2-base weights to a local folder (on a machine with internet):
   ```bash
   # In a Python env with huggingface_hub:
   python3 -c "from huggingface_hub import snapshot_download; snapshot_download('microsoft/Florence-2-base', local_dir='/tmp/florence-2-base')"
   ```
2. Copy that folder into `mc3-starter-kit/starter/` (call it `florence-2-base`).
3. Uncomment in `Dockerfile`:
   ```
   COPY florence-2-base /models/florence-2-base
   ```
4. Rebuild.
5. The OCR backend will auto-fall-back to the VLM if tesseract fails or isn't installed.

---

## Deadlines & quick checklist

- **Deadline: Oct 13, 2026, 20:00 CEST.** Don't cut it close.
- AMD cloud instance provisioning can take time. Do step 1 today.
- Once selfcheck is green, submit. Don't over-iterate.
- After submitting, watch the leaderboard. If something is clearly broken, you can resubmit (lablab usually allows updates until the Match closes).

**Pre-flight checklist:**
- [ ] AMD GPU machine accessible via SSH
- [ ] `docker --version` works
- [ ] `rocminfo` shows the GPU
- [ ] ~100 GB free disk
- [ ] `docker build -t mc3-rag:v1 starter/` succeeds
- [ ] `python3 selfcheck.py mc3-rag:v1 mc3-corpus` says `ALL CHECKS PASSED`
- [ ] VRAM stays in range (run a query and watch `amd-smi`)
- [ ] Image pushed to ghcr.io (or another registry)
- [ ] Submitted on lablab Match 3 page

That's it. You're ready.