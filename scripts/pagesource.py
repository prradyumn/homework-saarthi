"""Where a textbook page image comes from — including on a deployed server.

FR-10 shows the parent the actual page of their child's book. On this laptop that
is trivial: `render_pages.py` has already written 190 PNGs to `ingest/pages/`.
Deployment is where it stops being trivial, and the reason is not technical.

**The pages are not ours to ship.** Every page carries "© NCERT / not to be
republished", which is why `ingest/raw`, `ingest/pages` and `ingest/extracted` are
gitignored (HANDOFF §2). Baking 162 MB of rendered pages into a container image and
pushing it to a public host is exactly the republishing that line forbids — and a
portfolio project whose deployment step violates the licence of the content it is
built on is not a portfolio project I want to hand over.

So the deployed server resolves a page in three steps, cheapest first:

  1. `ingest/pages/pNNN.png`   — already rendered (this laptop, and only this laptop)
  2. `ingest/raw/<code>.pdf`   — render the one page we need, on demand
  3. ncert.nic.in              — fetch the chapter PDF from NCERT's own server,
                                 cache it, render the one page we need

Step 3 is what makes the deployed demo legal and complete at the same time: the
bytes reach the parent's browser from NCERT's copy, at request time, and nothing
is redistributed by us. It costs one cold fetch per chapter (~2 MB) and then
behaves like step 2 forever.

The derived artefacts we DO ship — `chunks.json`, the embeddings — are
transformations for retrieval, not a readable substitute for the book.
"""

from __future__ import annotations

import io
import json
import pathlib
import subprocess
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGES = ROOT / "ingest" / "pages"
RAW = ROOT / "ingest" / "raw"
# Deployed containers get a writable cache that is NOT the repo, so a read-only
# image and an ephemeral filesystem both behave.
CACHE = pathlib.Path(
    __import__("os").environ.get("SAATHI_CACHE", str(ROOT / "ingest" / "pdf_cache")))
MANIFEST = ROOT / "ingest" / "manifest.json"

BASE = "https://ncert.nic.in/textbook/pdf"

# 150 dpi, not render_pages.py's 300. That file feeds extraction, where every
# glyph matters; this one feeds a phone screen and is then downscaled to ~1000px
# wide anyway, so rendering at 300 would be work done only to throw away.
WEB_DPI = 150

# Seconds to wait on ncert.nic.in from inside a web request. Overridable so the
# ingest path, which legitimately can wait, is not bound by the web budget.
FETCH_TIMEOUT = int(__import__("os").environ.get("SAATHI_FETCH_TIMEOUT", "20"))

_lock = threading.Lock()
_map: dict[int, tuple[str, int]] | None = None


class PageUnavailable(Exception):
    """The page exists in the book but we could not produce an image of it."""


def _page_map() -> dict[int, tuple[str, int]]:
    """Printed book page -> (NCERT chapter code, 0-based index into that PDF).

    Built from the same manifest that `fetch_ncert.py` asserted against the
    printed table of contents (D0.2), so the mapping is checked, not assumed.
    """
    global _map
    if _map is not None:
        return _map
    out: dict[int, tuple[str, int]] = {}
    for ch in json.loads(MANIFEST.read_text()):
        if not ch.get("in_retrieval_corpus"):
            continue
        first = ch["pdf_page_first"]
        for n in range(ch["book_page_start"], ch["book_page_end"] + 1):
            out[n] = (ch["code"], first + (n - ch["book_page_start"]))
    _map = out
    return out


def _chapter_pdf(code: str) -> pathlib.Path:
    """The chapter PDF, from disk if we have it, else from NCERT, cached."""
    local = RAW / f"{code}.pdf"
    if local.exists() and local.stat().st_size > 40_000:
        return local
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"{code}.pdf"
    if dest.exists() and dest.stat().st_size > 40_000:
        return dest
    url = f"{BASE}/{code}.pdf"
    tmp = dest.with_suffix(".part")
    try:
        # curl for the same reason fetch_ncert.py uses it: ncert.nic.in stalls
        # often enough that a single urllib call is not a fetch strategy.
        #
        # The timeout is 20s, not the 120s the ingest scripts use, because this
        # one runs inside a web request. A parent who taps "पेज देखिए" must not
        # wait two minutes to find out the page is unavailable — and the fetch
        # holds `_lock`, so a stalled chapter blocks every other reader too.
        # ncert.nic.in was observed fully unreachable on 19 Sep 2026 (SSL connect
        # failure), which is exactly the case this bounds. The interface already
        # degrades: the page <img> has an onerror that swaps in a Hindi line.
        subprocess.run(["curl", "-sSL", "--max-time", str(FETCH_TIMEOUT),
                        "--connect-timeout", "8", "--retry", "1",
                        "-o", str(tmp), url], check=True, capture_output=True)
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise PageUnavailable(f"could not fetch {url}: {exc}") from exc
    if not tmp.exists() or tmp.stat().st_size < 40_000:
        tmp.unlink(missing_ok=True)
        raise PageUnavailable(f"{url} returned too little to be the chapter")
    tmp.replace(dest)
    return dest


def render(page: int) -> bytes:
    """PNG bytes for one printed page, by whichever route is available."""
    ready = PAGES / f"p{page:03d}.png"
    if ready.exists():
        return ready.read_bytes()

    entry = _page_map().get(page)
    if entry is None:
        raise PageUnavailable(f"page {page} is not a prose page of the corpus")
    code, idx = entry

    with _lock:  # one fetch per chapter, not one per concurrent reader
        pdf = _chapter_pdf(code)
    try:
        import pymupdf
    except ImportError as exc:  # noqa: F841
        raise PageUnavailable("pymupdf not installed") from None
    with pymupdf.open(pdf) as doc:
        if idx >= doc.page_count:
            raise PageUnavailable(f"{code} has {doc.page_count} pages, wanted {idx}")
        return doc[idx].get_pixmap(dpi=WEB_DPI).tobytes("png")



# ------------------------------------------------------------- cache warming
#
# ncert.nic.in is not reliable. It was fully unreachable (SSL connect failure,
# HTTP 000) twice within an hour on 19 Sep 2026, each time for several minutes.
# FR-10 — show the parent the actual page — is a P0 feature, and a lazy fetch
# means it works only if NCERT happens to be up at the moment a parent taps.
#
# Warming converts that into a much weaker requirement: NCERT must be up at SOME
# point after the box boots. The container fetches each chapter once, in the
# background, into its own cache — the same bytes from the same server it would
# fetch lazily, just eagerly. Nothing is redistributed and nothing is committed;
# the cache is ephemeral and gitignored.
#
# Deliberately sequential and unhurried: this is a courtesy to a government
# server, not a race. It never blocks a request, and failures are retried on the
# next sweep rather than raised.

WARM_INTERVAL = 600          # seconds between sweeps while anything is missing
_warming = False


def cache_state() -> dict:
    """Which chapters are already local, for the status endpoint and preflight."""
    codes = sorted({code for code, _ in _page_map().values()})
    have = [c for c in codes
            if (RAW / f"{c}.pdf").exists()
            or (CACHE / f"{c}.pdf").exists()]
    return {"chapters": len(codes), "cached": len(have),
            "missing": [c for c in codes if c not in have]}


def warm_cache(interval: int = WARM_INTERVAL) -> None:
    """Fetch every chapter not already on disk, forever, in the background.

    Runs in a daemon thread. Sweeps, sleeps, sweeps again — so an NCERT outage at
    boot costs a delay rather than a broken feature for the life of the container.
    """
    global _warming
    if _warming:
        return
    _warming = True

    def sweep() -> None:
        import time

        while True:
            state = cache_state()
            if not state["missing"]:
                print(f"  pages: all {state['chapters']} chapters cached", flush=True)
                return
            got = 0
            for code in state["missing"]:
                try:
                    with _lock:
                        _chapter_pdf(code)
                    got += 1
                except PageUnavailable:
                    pass          # NCERT is down or slow; try again next sweep
                time.sleep(1)     # be a polite client
            left = len(cache_state()["missing"])
            print(f"  pages: cached {got} chapter(s), {left} still missing"
                  + (f" — retrying in {interval}s" if left else ""), flush=True)
            if not left:
                return
            time.sleep(interval)

    threading.Thread(target=sweep, daemon=True, name="page-cache-warm").start()

# ---------------------------------------------------------------- web encoding
#
# §3.1: this parent's data is metered and intermittent, so the page image needs a
# GUARANTEED ceiling, not a typical size. One quality setting gives neither — the
# same encoder produced 89 KB for p105 and 147 KB for p111, because weight follows
# how much is drawn on the page. Step quality down, then width, until the encoded
# bytes actually fit.
MAX_BYTES = 130 * 1024
_QUALITY_STEPS = (78, 68, 58, 48)
_WIDTH_STEPS = (1000, 850)


def for_web(page: int) -> tuple[bytes, str]:
    """A phone-sized, budget-capped JPEG of one printed page."""
    raw = render(page)
    try:
        from PIL import Image

        original = Image.open(io.BytesIO(raw)).convert("RGB")
        best = None
        for width in _WIDTH_STEPS:
            img = original
            if img.width > width:
                img = img.resize((width, round(img.height * width / img.width)),
                                 Image.LANCZOS)
            for quality in _QUALITY_STEPS:
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
                data = buf.getvalue()
                if best is None or len(data) < len(best):
                    best = data
                if len(data) <= MAX_BYTES:
                    return data, "image/jpeg"
        return best, "image/jpeg"
    except ImportError:
        # Degrade rather than break: the page is still readable, just heavier.
        return raw, "image/png"


if __name__ == "__main__":
    import sys

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 18
    data, ctype = for_web(n)
    print(f"page {n}: {len(data) / 1024:.0f} KB {ctype}")
