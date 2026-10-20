"""OCR backend. Lazy-loaded; never loaded at index time.

Default backend: tesseract via pytesseract if tesseract is on PATH.
If tesseract is unavailable, falls back to the configured VLM.

Inside the container the tesseract binary is small (~30 MB) and
pytesseract is a thin wrapper, so the whole thing adds ~50 MB. The
VLM backend (Florence-2-base via transformers) is ~1.5 GB and only
loaded if tesseract isn't available.

Either way this is loaded inside the per-question process ONLY when
the query needs OCR. The model stays resident in memory for the
process's lifetime but each per-question process is short-lived, so
the cold-start cost matters. We cache OCR results in the persistent
index so subsequent queries don't re-run it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_BACKEND: str | None = None
_TESSERACT_OK: bool | None = None
_VLM = None
_VLM_PROCESSOR = None


def _tesseract_available() -> bool:
    global _TESSERACT_OK
    if _TESSERACT_OK is None:
        _TESSERACT_OK = shutil.which("tesseract") is not None
    return _TESSERACT_OK


def _ocr_tesseract(image_path: str) -> str:
    if not _tesseract_available():
        raise RuntimeError("tesseract not installed")
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as out:
        out_path = out.name
    try:
        subprocess.run(
            ["tesseract", image_path, out_path[:-4], "-l", "eng", "--psm", "6"],
            check=True,
            timeout=20,
            capture_output=True,
        )
        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _ocr_vlm(image_path: str) -> str:
    """Florence-2-base for OCR. Lazy import + lazy load."""
    global _VLM, _VLM_PROCESSOR
    if _VLM is None:
        # config: FLORENCE2_MODEL env var, default to /models/florence-2-base
        model_path = os.environ.get("FLORENCE2_MODEL", "/models/florence-2-base")
        import torch  # provided by base image
        from transformers import AutoProcessor, AutoModelForCausalLM
        _VLM_PROCESSOR = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
        # device: ROCm torch exposes .cuda(); falls back to cpu if no GPU
        _VLM = AutoModelForCausalLM.from_pretrained(
            model_path, trust_remote_code=True, torch_dtype=torch.float16
        ).to("cuda" if torch.cuda.is_available() else "cpu").eval()
    from PIL import Image
    image = Image.open(image_path).convert("RGB")
    import torch
    prompt = "<OCR>"
    inputs = _VLM_PROCESSOR(text=prompt, images=image, return_tensors="pt").to(_VLM.device)
    with torch.no_grad():
        ids = _VLM.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=512,
            num_beams=3,
        )
    text = _VLM_PROCESSOR.batch_decode(ids, skip_special_tokens=True)[0]
    # Florence-2 wraps OCR output in <OCR_WITH_REGION>...</OCR_WITH_REGION> sometimes
    text = text.replace("<OCR>", "").replace("</OCR>", "")
    return text.strip()


def ocr_image(image_path: str) -> str:
    """Run OCR on a corpus-relative image path. Returns plaintext.

    Tries tesseract first; falls back to Florence-2 if installed and tesseract
    isn't there. Returns empty string if neither works (caller will record).
    """
    # image_path may be corpus-relative; the caller passes the absolute path
    abs_path = str(image_path)
    if _tesseract_available():
        try:
            return _ocr_tesseract(abs_path)
        except Exception:
            pass
    try:
        return _ocr_vlm(abs_path)
    except Exception:
        return ""


def ocr_for_corpus(corpus_root: Path, rel_path: str) -> str:
    return ocr_image(str(corpus_root / rel_path))