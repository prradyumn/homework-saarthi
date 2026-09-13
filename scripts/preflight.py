"""Is this thing actually deployable, and is a deployed one actually working?

Two modes, because "it runs on my laptop" and "the link works" are different
claims and the second is the one that matters for a demo:

    python scripts/preflight.py                     # can this box serve?
    python scripts/preflight.py --url https://…     # is that box serving?

Local mode checks the things a container silently gets wrong: a missing artefact,
a chunk-id collision, a model that is not cached, a page route that cannot reach
NCERT. Remote mode checks a live deployment the way a visitor would.

Exit code is the number of failures, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

OK, BAD, WARN = [], [], []


def check(name: str, ok: bool, detail: str = "", *, warn_only: bool = False) -> bool:
    if ok:
        OK.append(name)
        mark = "ok  "
    elif warn_only:
        WARN.append(name)
        mark = "warn"
    else:
        BAD.append(name)
        mark = "FAIL"
    print(f"  {mark} {name}" + (f"  — {detail}" if detail else ""), flush=True)
    return ok


# --------------------------------------------------------------------- local
def local() -> None:
    print("\n  artefacts the image must carry\n")
    needed = {
        "ingest/chunks.json": "the Class 5 corpus, chunked",
        "ingest/decoy_chunks.json": "the out-of-syllabus decoys — without these the "
                                    "syllabus gate is vacuous",
        "ingest/index_combined.npz": "embeddings for both",
        "ingest/manifest.json": "printed page -> chapter PDF map, for FR-10",
        "web/index.html": "the interface",
    }
    for rel, why in needed.items():
        f = ROOT / rel
        check(rel, f.exists() and f.stat().st_size > 1000,
              why if not f.exists() else f"{f.stat().st_size / 1024:.0f} KB")

    print("\n  the corpus loads, and the D14 id-collision assertion still holds\n")
    try:
        import json as _json

        from answer import CHUNKS, DECOYS, load_chunks

        # load_chunks() keys by id and ASSERTS uniqueness itself (D14: a silent
        # collision once hid 74 of 248 chunks from retrieval). So the real check is
        # that it returns everything both files contain — if the assertion inside
        # it were ever relaxed, this comparison would still catch the loss.
        by_id = load_chunks()
        on_disk = len(_json.loads(CHUNKS.read_text(encoding="utf-8")))
        on_disk += len(_json.loads(DECOYS.read_text(encoding="utf-8"))) if DECOYS.exists() else 0
        in_syllabus = sum(1 for c in by_id.values() if c.get("in_syllabus"))
        check("chunks load", len(by_id) > 200,
              f"{len(by_id)} chunks ({in_syllabus} Class 5, "
              f"{len(by_id) - in_syllabus} decoys)")
        check("no chunk was dropped by an id collision", len(by_id) == on_disk,
              f"{on_disk - len(by_id)} lost between disk and memory")
    except Exception as exc:  # noqa: BLE001
        check("chunks load", False, str(exc)[:150])

    print("\n  query embedding — the one thing that is not precomputed\n")
    import embedder

    info = embedder.available()
    check(f"embedder configured ({info['backend']})", info["ok"],
          info.get("reason", info.get("model", ""))[:150])
    try:
        t0 = time.time()
        v = embedder.embed(["तैयारी"])
        import numpy as _np

        check("a query embeds to a unit 1024-vector",
              v.shape == (1, 1024) and abs(float(_np.linalg.norm(v[0])) - 1) < 1e-3,
              f"{v.shape} in {time.time() - t0:.1f}s")
    except Exception as exc:  # noqa: BLE001
        check("a query embeds", False, str(exc)[:150])

    try:
        t0 = time.time()
        from answer import retrieve

        hits, _ = retrieve("तैयारी")
        check("retrieval returns hits", bool(hits), f"{time.time() - t0:.1f}s")
    except Exception as exc:  # noqa: BLE001
        check("retrieval returns hits", False, str(exc)[:150])

    print("\n  FR-10 page images — including the route a deployed box actually uses\n")
    import pagesource

    try:
        data, ctype = pagesource.for_web(18)
        check("a page renders", len(data) > 10_000, f"{len(data) / 1024:.0f} KB {ctype}")
        check("page is under the metered-data budget",
              len(data) <= pagesource.MAX_BYTES,
              f"{len(data) / 1024:.0f} KB / {pagesource.MAX_BYTES / 1024:.0f} KB")
    except Exception as exc:  # noqa: BLE001
        check("a page renders", False, str(exc)[:150])

    # The deployed container has no ingest/pages and no ingest/raw. That route is
    # the one that breaks in production and works on a laptop, so test it here by
    # taking both local sources away.
    pages, raw = pagesource.PAGES, pagesource.RAW
    pagesource.PAGES = pathlib.Path("/nonexistent")
    pagesource.RAW = pathlib.Path("/nonexistent")
    try:
        t0 = time.time()
        data, _ = pagesource.for_web(20)
        check("deployed page route reaches ncert.nic.in", len(data) > 10_000,
              f"{len(data) / 1024:.0f} KB in {time.time() - t0:.1f}s")
    except Exception as exc:  # noqa: BLE001
        check("deployed page route reaches ncert.nic.in", False, str(exc)[:150])
    finally:
        pagesource.PAGES, pagesource.RAW = pages, raw

    print("\n  credentials — each one degrades to something honest if absent\n")
    import os

    from answer import _load_env

    _load_env()
    # Phrased as the capability, not the variable: a line reading
    # "GEMINI_API_KEY set — warn" against a box where it is deliberately unset
    # states the opposite of the truth.
    check("generation (GROQ_API_KEY)", bool(os.environ.get("GROQ_API_KEY")),
          "REQUIRED — without it nothing can be answered at all")

    if embedder.backend() == "cloudflare":
        check("query embedding (CF_ACCOUNT_ID + CF_API_TOKEN)",
              bool(os.environ.get("CF_ACCOUNT_ID") and os.environ.get("CF_API_TOKEN")),
              "REQUIRED on a box with no local model — nothing can be retrieved")

    gemini = bool(os.environ.get("GEMINI_API_KEY"))
    check("figure reading (GEMINI_API_KEY)", gemini,
          "on" if gemini else
          "off by design (D18) — picture-only questions are refused with a reason",
          warn_only=True)

    voice = bool(os.environ.get("BHASHINI_USER_ID")
                 and os.environ.get("BHASHINI_API_KEY"))
    check("voice (Bhashini)", voice,
          "on" if voice else "not configured — falls back to the browser speech API",
          warn_only=True)

    print("\n  the book is not in the image, and neither are the keys\n")
    # Two ways to satisfy this, and absence is the stronger one. In the source
    # repo these directories exist and .dockerignore keeps them out of the image;
    # in a generated deploy/ folder they were never copied at all. An earlier
    # version of this check only looked at .dockerignore, and so reported four
    # failures against a folder that was in fact cleaner than the repo.
    ignore = (ROOT / ".dockerignore").read_text() if (ROOT / ".dockerignore").exists() else ""
    for d in ("ingest/raw/", "ingest/pages/", "ingest/extracted/", "ingest/pdf_cache/"):
        present = (ROOT / d).exists()
        check(f"{d} stays out of the image",
              not present or d in ignore,
              "absent" if not present else "present but excluded by .dockerignore")
    env = ROOT / ".env"
    check(".env stays out of the image",
          not env.exists() or "\n.env" in "\n" + ignore,
          "absent — secrets arrive as environment variables" if not env.exists()
          else "present but excluded by .dockerignore")


# -------------------------------------------------------------------- remote
def remote(url: str) -> None:
    import urllib.request

    url = url.rstrip("/")
    print(f"\n  checking the live deployment at {url}\n")

    def get(path: str, timeout: int = 30):
        req = urllib.request.Request(url + path, headers={"User-Agent": "saathi-preflight"})
        return urllib.request.urlopen(req, timeout=timeout)

    try:
        t0 = time.time()
        with get("/api/health", timeout=20) as r:
            health = json.loads(r.read())
        check("responds to /api/health", r.status == 200, f"{time.time() - t0:.1f}s")
    except Exception as exc:  # noqa: BLE001
        check("responds to /api/health", False, str(exc)[:150])
        return

    try:
        with get("/api/status") as r:
            st = json.loads(r.read())
        check("models are loaded", bool(st.get("ready")),
              "still warming up — wait and re-run" if not st.get("ready") else "")
        check("a real generation backend is configured", st.get("backend") != "stub",
              f"backend={st.get('backend')} — stub answers are canned text")
        check("figure reading available", bool(st.get("vision", {}).get("ok")),
              "chart questions will be refused", warn_only=True)
        check("Bhashini voice available", bool(st.get("voice", {}).get("ok")),
              "falls back to the browser speech API", warn_only=True)
    except Exception as exc:  # noqa: BLE001
        check("reads /api/status", False, str(exc)[:150])

    try:
        with get("/") as r:
            html = r.read().decode()
        check("serves the interface", "होमवर्क साथी" in html, f"{len(html) / 1024:.0f} KB")
    except Exception as exc:  # noqa: BLE001
        check("serves the interface", False, str(exc)[:150])

    try:
        t0 = time.time()
        with get("/page/18", timeout=90) as r:
            img = r.read()
        check("FR-10 serves a textbook page", len(img) > 10_000,
              f"{len(img) / 1024:.0f} KB in {time.time() - t0:.1f}s "
              f"(first hit per chapter fetches from ncert.nic.in)")
        check("page image respects the metered-data budget", len(img) < 150_000,
              f"{len(img) / 1024:.0f} KB")
    except Exception as exc:  # noqa: BLE001
        check("FR-10 serves a textbook page", False, str(exc)[:150])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="check a deployed instance instead of this box")
    args = ap.parse_args()

    remote(args.url) if args.url else local()

    print(f"\n  {len(OK)} ok, {len(WARN)} warnings, {len(BAD)} failures")
    if WARN:
        print("  warnings are degraded-but-honest paths, not blockers:")
        for w in WARN:
            print(f"    · {w}")
    if BAD:
        print("  blockers:")
        for b in BAD:
            print(f"    · {b}")
    print()
    return len(BAD)


if __name__ == "__main__":
    sys.exit(main())
