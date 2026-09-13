"""Where a query vector comes from — locally, or from Cloudflare Workers AI.

The corpus index (`ingest/index_combined.npz`) was embedded once with BGE-M3 and
ships precomputed. Only the *query* needs embedding at request time, and that one
call is what forced a 4 GB container: BGE-M3 loads at ~1.9 GB and peaks near
3.3 GB while encoding, which ruled out every free host with 512 MB.

Hugging Face locked Docker Spaces behind PRO in July 2026, taking the last free
tier with that much memory. So the query embedding moves off the box:

    local        sentence-transformers + BGE-M3 on this machine.  DEFAULT.
                 Every eval runs here, needs no network and no credential, and
                 stays byte-for-byte reproducible.
    cloudflare   `@cf/baai/bge-m3` on Workers AI. THE SAME MODEL, which is the
                 whole point — a different embedder would invalidate the shipped
                 index and every number measured against it.

Free allocation is 10,000 Neurons/day with no card, and bge-m3 costs 1,075
neurons per million input tokens. A query runs ~30 tokens, so the ceiling is on
the order of 300,000 queries/day — against Groq's ~90 answers/day, embeddings
will never be the binding limit.

**Compatibility is asserted, not assumed.** Two services running "the same model"
can still differ in pooling or normalisation, and a query vector that is subtly
off does not throw — it silently returns the wrong passage and every measured
figure quietly stops being true. `python scripts/embedder.py --compare` embeds
the same strings both ways and reports cosine agreement; anything below 0.999 is
treated as a different model.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent

MODEL = os.environ.get("SAATHI_EMBED_MODEL", "BAAI/bge-m3")
CF_MODEL = "@cf/baai/bge-m3"
DIM = 1024

# local unless told otherwise; a deployed box sets SAATHI_EMBED=cloudflare
BACKEND = os.environ.get("SAATHI_EMBED", "").strip().lower() or None


class NotConfigured(Exception):
    """The chosen backend has no credentials."""


class EmbedError(Exception):
    """The backend was reachable but did not return a usable vector."""


def _load_env() -> None:
    """Read .env if present. Real environment variables win, so a host's secrets
    are never shadowed by a file that should not be in the image anyway."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


# --------------------------------------------------------------------- local
_model = None


def _embed_local(texts: list[str]) -> np.ndarray:
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL, device="cpu")
    return _model.encode(texts, batch_size=4, normalize_embeddings=True,
                         convert_to_numpy=True)


# ---------------------------------------------------------------- cloudflare
def _cf_credentials() -> tuple[str, str]:
    _load_env()
    account = os.environ.get("CF_ACCOUNT_ID", "").strip()
    token = os.environ.get("CF_API_TOKEN", "").strip()
    if not account or not token:
        raise NotConfigured(
            "Set CF_ACCOUNT_ID and CF_API_TOKEN to embed via Cloudflare Workers AI. "
            "Free, no card: dash.cloudflare.com -> AI -> Workers AI, then create an "
            "API token with the 'Workers AI' read permission.")
    return account, token


def _embed_cloudflare(texts: list[str]) -> np.ndarray:
    account, token = _cf_credentials()
    url = (f"https://api.cloudflare.com/client/v4/accounts/{account}"
           f"/ai/run/{CF_MODEL}")

    # Token in a header FILE, never argv: argv is readable by every process on the
    # machine and lands in shell history. Same discipline as vision.py and
    # answer.py, and this project has already leaked three keys.
    hfd, hpath = tempfile.mkstemp()
    bfd, bpath = tempfile.mkstemp()
    try:
        os.write(hfd, f"Authorization: Bearer {token}\n".encode())
        os.close(hfd)
        os.write(bfd, json.dumps({"text": texts}).encode())
        os.close(bfd)
        r = subprocess.run(
            ["curl", "-sS", "--max-time", "30", "-X", "POST", url,
             "-H", f"@{hpath}", "-H", "Content-Type: application/json",
             "--data-binary", f"@{bpath}"],
            capture_output=True, text=True)
    finally:
        os.unlink(hpath)
        os.unlink(bpath)

    if r.returncode != 0:
        raise EmbedError(f"could not reach Workers AI: {r.stderr[:200]}")
    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise EmbedError(f"Workers AI returned non-JSON: {r.stdout[:200]}") from None
    if not payload.get("success", False):
        errs = payload.get("errors") or payload
        raise EmbedError(f"Workers AI refused: {str(errs)[:250]}")

    data = payload.get("result", {}).get("data")
    if not data or len(data) != len(texts):
        raise EmbedError(f"expected {len(texts)} vectors, got {data and len(data)}")

    v = np.asarray(data, dtype=np.float32)
    if v.shape[1] != DIM:
        raise EmbedError(f"expected {DIM}-dim vectors, got {v.shape[1]} — the index "
                         f"is BGE-M3 and cannot be queried with another model")
    # The shipped index is L2-normalised so a dot product IS cosine. Normalise
    # here too rather than trusting the provider to have done it.
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    return v / np.maximum(norms, 1e-12)


BACKENDS = {"local": _embed_local, "cloudflare": _embed_cloudflare}


def _choose() -> str:
    if BACKEND:
        if BACKEND not in BACKENDS:
            raise EmbedError(f"unknown SAATHI_EMBED={BACKEND!r}; "
                             f"choose one of {sorted(BACKENDS)}")
        return BACKEND
    # No explicit choice: prefer local, but a slim container has no torch at all,
    # and falling back is better than crashing on an import it was built without.
    try:
        import sentence_transformers  # noqa: F401

        return "local"
    except ImportError:
        return "cloudflare"


def embed(texts: list[str]) -> np.ndarray:
    """Unit-norm BGE-M3 vectors, whichever backend this box is configured for."""
    return BACKENDS[_choose()](texts)


def backend() -> str:
    return _choose()


def available() -> dict:
    """For the interface and preflight to branch on, without embedding anything."""
    b = _choose()
    if b == "cloudflare":
        try:
            _cf_credentials()
        except NotConfigured as exc:
            return {"ok": False, "backend": b, "reason": str(exc)}
    return {"ok": True, "backend": b, "model": MODEL}


# ------------------------------------------------------------------ compare
SAMPLES = [
    "1 किलोग्राम में कितने ग्राम होते हैं?",
    "तुल्य भिन्न क्या होती है?",
    "सम पंचभुज से टाइल क्यों नहीं बनती?",
    "नक्शे में जगह कैसे ढूँढ़ते हैं?",
    "how many grams are in one kilogram?",
]


def compare() -> int:
    """Do the two backends agree closely enough to share one index?

    A query vector that is subtly wrong does not raise — it quietly retrieves the
    wrong passage, and every figure measured against the shipped index stops being
    true without anything failing. So this is a gate, not a diagnostic.
    """
    print(f"\n  embedding {len(SAMPLES)} strings both ways\n")
    local = _embed_local(SAMPLES)
    remote = _embed_cloudflare(SAMPLES)

    cos = np.sum(local * remote, axis=1)
    worst = float(cos.min())
    for s, c in zip(SAMPLES, cos):
        flag = "ok  " if c >= 0.999 else "FAIL"
        print(f"  {flag} cos={c:.6f}  {s[:46]}")

    # The real question is not whether the vectors match to 6 places, but whether
    # they rank the corpus the same way. Check that too.
    index = ROOT / "ingest" / "index_combined.npz"
    rank_note = ""
    if index.exists():
        data = np.load(index, allow_pickle=False)
        vectors = data["vectors"]
        agree = 0
        for i in range(len(SAMPLES)):
            top_l = np.argsort(-(vectors @ local[i]))[:5]
            top_r = np.argsort(-(vectors @ remote[i]))[:5]
            agree += int(list(top_l) == list(top_r))
        rank_note = f"  identical top-5 ranking on {agree}/{len(SAMPLES)} queries"
        print(f"\n{rank_note}")

    ok = worst >= 0.999
    print(f"\n  worst cosine agreement: {worst:.6f}"
          f"   -> {'SAME MODEL — index stays valid' if ok else 'DIFFERENT — do not ship'}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--compare", action="store_true",
                    help="verify Cloudflare agrees with local BGE-M3")
    args = ap.parse_args()

    if args.compare:
        sys.exit(compare())
    print(json.dumps(available(), ensure_ascii=False, indent=2))
    v = embed(["तैयारी"])
    print(f"  {backend()}: {v.shape} ‖v‖={float(np.linalg.norm(v[0])):.6f}")
