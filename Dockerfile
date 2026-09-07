# Homework Saathi — the deployable prototype.
#
# Target: Hugging Face Spaces (Docker SDK), because it is the only free tier with
# enough memory to hold BGE-M3. The model needs ~1.9 GB to load and peaks near
# 3.3 GB while encoding; Render, Fly and Vercel free tiers give 256 MB – 1 GB and
# cannot run this at all. HF Spaces free gives 2 vCPU / 16 GB. See DEPLOY.md.
#
# Nothing here is HF-specific except the default port, so any container host with
# ~4 GB of RAM will run the same image.

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

# The CPU wheel index. The default torch wheel bundles the CUDA runtime — about
# 2 GB of GPU libraries on a box with no GPU.
COPY requirements-deploy.txt .
RUN pip install --no-cache-dir torch==2.14.0 \
      --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements-deploy.txt

# Bake the embedding model into the image rather than downloading it on boot.
# 2.1 GB over the network at container start is several minutes during which the
# app is up but every question 503s — the worst possible state for a demo link.
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('BAAI/bge-m3').encode(['तैयारी'])" \
    && chown -R saathi:saathi /home/saathi/.cache

COPY --chown=saathi:saathi scripts/ ./scripts/
COPY --chown=saathi:saathi web/ ./web/
COPY --chown=saathi:saathi ingest/ ./ingest/

USER saathi

ENV PORT=7860 \
    HOST=0.0.0.0 \
    SAATHI_BACKEND=groq \
    SAATHI_CACHE=/tmp/saathi-pdf-cache

EXPOSE 7860

# /api/health answers as soon as the process is up; /api/status reports whether
# the models have finished loading. The orchestrator wants the first question —
# probing readiness here would kill the container during a legitimate warm-up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/api/health',timeout=4).status==200 else 1)"

CMD ["python", "scripts/serve.py"]
