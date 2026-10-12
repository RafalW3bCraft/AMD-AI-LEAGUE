# Match 3 submission container.
# Built on the ROCm base image mandated by the challenge.
# Layer identity is checked by the harness -- do not change FROM.
FROM rocm/pytorch:rocm10.0_ubuntu26.04_py3.14_pytorch_release_2.13.0

# System tools: tesseract is our primary OCR backend (small, accurate, runs on CPU).
# adduser below matches the recommended non-root practice for the harness.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libtesseract-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CRITICAL: --no-deps on every pip install. pip WILL silently replace the
# ROCm torch build with a CUDA build otherwise; see CONTRACT.md.
# pypdf / python-docx / openpyxl / Pillow / pytesseract have no torch dep
# but we still pass --no-deps defensively.
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --no-deps -r /app/requirements.txt

COPY app/ /app/

# Optional: Florence-2 OCR weights. Ship ONLY if you choose the VLM path.
# Default path uses tesseract (above), so this stays commented.
# COPY models/florence-2-base /models/florence-2-base

# Harness runs the container as a long-lived process and exec's our script.
RUN mkdir -p /app/corpus /app/output /app/index
CMD ["sleep", "infinity"]