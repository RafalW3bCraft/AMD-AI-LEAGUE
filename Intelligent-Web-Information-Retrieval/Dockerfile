# AMD AI League — Match 4 container
#
# Base image: ROCm PyTorch (mandated for the AMD AI League; same tag as
# used in Match 2 and Match 3 starter kits, which our team confirmed by
# reading:
#   - github.com/RafalW3bCraft/Citation-RAG
#   - github.com/eric-ljf/amd-match3-rag
#   - github.com/helshahaby/EvidenceRAG
#   - github.com/SamruddhiBhagwat24/amd-ai-match3-rag
#   - github.com/tatianamartins-VYSON/mc3-rag
#
# IMPORTANT: do NOT `pip install torch torchvision torchaudio` here.
# The base image ships the ROCm build; pip would silently replace it
# with a CUDA build and the AMD GPU would become invisible. (This is
# exactly the trap the Match 3 starter-kit authors warn about.)
#
# Iter 4 changes:
#   - ENTRYPOINT routes through the canonical /app/app.py wrapper so
#     both `docker run match4-amd-agent` and `docker run match4-amd-agent
#     python3 -m app.cli` work.
#   - Verification uses torch.version.hip (real attribute) instead of
#     a brittle substring check on torch.__version__.
#   - HEALTHCHECK actually exercises the CLI in --help mode.
#   - We add /app/corpus and /app/output as proper VOLUME mounts so the
#     AMD grader can bind-mount them.
#   - PYTHONHASHSEED=random is NOT set; we keep PYTHONUNBUFFERED for
#     live log streaming.

FROM rocm/pytorch:rocm10.0_ubuntu26.04_py3.14_pytorch_release_2.13.0

LABEL org.opencontainers.image.title="match4-amd-agent"
LABEL org.opencontainers.image.description="AMD AI League Match 4 - Intelligent Web Information Retrieval"
LABEL org.opencontainers.image.source="https://lablab.ai/ai-hackathons/amd-lablab-ai-academy-challenge"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.vendor="match4-amd-agent"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MATCH4_OUTPUT_DIR=/app/output \
    MATCH4_QUERY_DEADLINE_S=28 \
    MATCH4_OUTPUT_RESERVE_S=2 \
    MATCH4_ALLOW_LOCAL_NETWORK=0

WORKDIR /app

# Install OS deps that some optional features need (kept minimal).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
        build-essential gcc \
        libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Python deps — installed against the ROCm torch already in the base image.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-deps -r /app/requirements.txt && \
    pip install --no-cache-dir aiohttp==3.10.10 beautifulsoup4==4.12.3 lxml==5.3.0 pytest==9.1.1 pytest-asyncio==1.4.0

# Application code: the package (app/app/) and the entrypoint script
# (app/app.py) live in the repo's app/ directory. We copy them into
# /app/ so the canonical Match 4 layout is:
#   /app/app.py
#   /app/app/{cli.py,__init__.py,__main__.py,agent/,server.py}
#   /app/tests/...
#   /app/pytest.ini
COPY app/app.py /app/app.py
COPY app/__init__.py /app/app/__init__.py
COPY app/cli.py /app/app/cli.py
COPY app/__main__.py /app/app/__main__.py
COPY app/server.py /app/app/server.py
COPY app/agent /app/app/agent
COPY tests /app/tests
COPY pytest.ini /app/pytest.ini

# Output + corpus dirs (grader mounts corpus at /app/corpus, writes to /app/output).
RUN mkdir -p /app/output /app/corpus

# Verify the base image still contains the ROCm torch.
# Use real attribute checks (not substring matches on __version__).
RUN python3 -c "import torch; \
    assert getattr(torch.version, 'hip', None) is not None, f'no ROCm HIP: {torch.__version__}'; \
    print('torch', torch.__version__, 'hip', torch.version.hip)" \
    || (echo 'ROCm torch check FAILED — base image does not include HIP' && exit 1)

# Sanity: the CLI starts in --help mode.
RUN python3 /app/app.py --help > /dev/null && echo 'app.py --help OK'

# Default ENTRYPOINT is the canonical /app/app.py wrapper.
ENTRYPOINT ["python3", "/app/app.py"]

# Default CMD: grader-style. Override per phase as needed.
#   docker run match4-amd-agent --init
#   docker run match4-amd-agent --query-id q01 --query "..."
CMD ["--help"]

# Healthcheck: confirm the CLI starts in --help mode (does not hit the network).
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python3 /app/app.py --help > /dev/null || exit 1