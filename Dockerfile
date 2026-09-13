# Homework Saathi — the deployable prototype.
#
# This image deliberately carries no model. BGE-M3 needed ~1.9 GB to load and
# peaked near 3.3 GB while encoding, which meant a 4 GB host — and in July 2026
# Hugging Face moved Docker Spaces, the last free tier that size, behind PRO.
#
# So the query embedding goes to Cloudflare Workers AI instead, using the SAME
# model the index was built with (`@cf/baai/bge-m3`), which keeps the shipped
# vectors and every measured number valid. What is left runs in well under
# 512 MB, which fits the free tiers that need no card. See DECISIONS D19.

FROM python:3.12-slim

# curl: pagesource.py shells out to it to fetch a chapter PDF from ncert.nic.in
# the first time someone asks to see a page from that chapter.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces runs the container as uid 1000. Creating that user here means the
# model cache and the PDF cache are writable at runtime instead of failing on the
# first request with a permission error nobody sees until the demo is live.
RUN useradd -m -u 1000 saathi
ENV HOME=/home/saathi \
    HF_HOME=/home/saathi/.cache/huggingface \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements-deploy.txt .
RUN pip install --no-cache-dir -r requirements-deploy.txt

COPY --chown=saathi:saathi scripts/ ./scripts/
COPY --chown=saathi:saathi web/ ./web/
COPY --chown=saathi:saathi ingest/ ./ingest/

USER saathi

ENV PORT=7860 \
    HOST=0.0.0.0 \
    SAATHI_BACKEND=groq \
    SAATHI_EMBED=cloudflare \
    SAATHI_CACHE=/tmp/saathi-pdf-cache

EXPOSE 7860

# /api/health answers as soon as the process is up; /api/status reports whether
# the models have finished loading. The orchestrator wants the first question —
# probing readiness here would kill the container during a legitimate warm-up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/api/health',timeout=4).status==200 else 1)"

CMD ["python", "scripts/serve.py"]
