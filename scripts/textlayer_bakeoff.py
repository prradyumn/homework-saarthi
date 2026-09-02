"""Text-layer acceptance gate (PRD §11.4, extended).

The NCERT Hindi PDFs ship an embedded text layer, but its fonts (Kokila subsets,
Identity-H) have broken ToUnicode maps, so naive extraction silently substitutes
characters: भुजाओं -> िुजाओं, स्थान -> ्लथान, क्या -> ्‍तया. Corrupt chunk text
would degrade retrieval AND get pasted into the generation prompt as grounding,
which the §7.3 ungrounded-claim guardrail cannot detect because the citation is
genuine. So nothing gets chunked until an extraction path clears a measured bar.

Scored per method, against hand-transcribed ground truth (eval/ground_truth):

  prose CER   - character error rate on flowing sentences, via best-window
                alignment (each reference line matched to its closest substring
                in the method's output, so reading order is not penalised)
  numeric     - of the numbers appearing in reference prose, the share the
                method reproduces exactly in the matched window. Weighted
                separately because a wrong digit is the failure mode that
                §8.1 exists to prevent.
  fractions   - share of the page's stacked fractions recovered as n/d

Usage: python scripts/textlayer_bakeoff.py [--methods a,b] [--no-vlm]
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import re
import subprocess
import sys
import time
import unicodedata

import pymupdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "ingest" / "raw"
PAGES = ROOT / "ingest" / "pages"
GT = ROOT / "eval" / "ground_truth" / "pages.json"
OUT = ROOT / "eval" / "textlayer_bakeoff.json"
DUMP = ROOT / "ingest" / "extracted"

VLM_MODEL = "qwen2.5vl:7b"
VLM_PROMPT = (
    "Transcribe every word of Hindi text on this textbook page verbatim, in natural "
    "reading order. Output only the transcribed text, no commentary, no translation. "
    "Write a stacked fraction (numerator above denominator) inline as 1/2. "
    "Ignore the diagonal watermark."
)


# ---------------------------------------------------------------- normalisation
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)


def norm(text: str) -> str:
    """NFC, drop zero-width joiners, collapse whitespace. Applied to both sides so
    invisible formatting differences do not count as errors."""
    text = unicodedata.normalize("NFC", text or "")
    text = text.translate(ZERO_WIDTH)
    return re.sub(r"\s+", " ", text).strip()


NUM = re.compile(r"\d[\d,]*")


def numbers(text: str) -> list[str]:
    return [n.replace(",", "") for n in NUM.findall(text)]


# ------------------------------------------------------------------- extractors
def locate(book_page: int, manifest: list[dict]) -> tuple[str, int]:
    for e in manifest:
        if e["in_retrieval_corpus"] and e["book_page_start"] <= book_page <= e["book_page_end"]:
            return e["code"], book_page - e["page_offset"]
    raise KeyError(book_page)


def extract_pypdf(book_page: int, manifest: list[dict]) -> str:
    import pypdf

    code, idx = locate(book_page, manifest)
    return pypdf.PdfReader(RAW / f"{code}.pdf").pages[idx].extract_text() or ""


def extract_pymupdf(book_page: int, manifest: list[dict]) -> str:
    code, idx = locate(book_page, manifest)
    return pymupdf.open(RAW / f"{code}.pdf")[idx].get_text()


def extract_pdftotext(book_page: int, manifest: list[dict]) -> str:
    code, idx = locate(book_page, manifest)
    proc = subprocess.run(
        ["pdftotext", "-layout", "-f", str(idx + 1), "-l", str(idx + 1),
         str(RAW / f"{code}.pdf"), "-"],
        capture_output=True, text=True,
    )
    return proc.stdout


def _tesseract(book_page: int, lang: str) -> str:
    png = PAGES / f"p{book_page:03d}.png"
    proc = subprocess.run(
        ["tesseract", str(png), "stdout", "-l", lang, "--psm", "3"],
        capture_output=True, text=True,
    )
    return proc.stdout


def extract_tesseract(book_page: int, _manifest: list[dict]) -> str:
    return _tesseract(book_page, "hin")


def extract_tesseract_hin_eng(book_page: int, _manifest: list[dict]) -> str:
    """Adding eng recovers the × glyph but NOT the digits: the Devanagari model
    reads '1' as danda (।/॥) and deletes it, so 150 -> 50 and 1,000 -> ,000."""
    return _tesseract(book_page, "hin+eng")


def extract_vlm(book_page: int, _manifest: list[dict]) -> str:
    png = PAGES / f"p{book_page:03d}.png"
    payload = {
        "model": VLM_MODEL,
        "prompt": VLM_PROMPT,
        "images": [base64.b64encode(png.read_bytes()).decode()],
        "stream": False,
        "options": {"temperature": 0, "num_predict": 2048},
    }
    proc = subprocess.run(
        ["curl", "-s", "--max-time", "900", "http://localhost:11434/api/generate",
         "-d", "@-"],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    try:
        return json.loads(proc.stdout).get("response", "")
    except json.JSONDecodeError:
        return ""


def extract_hybrid(book_page: int, manifest: list[dict]) -> str:
    from extract_hybrid import hybrid_page

    code, idx = locate(book_page, manifest)
    page = pymupdf.open(RAW / f"{code}.pdf")[idx]
    return hybrid_page(page, PAGES / f"p{book_page:03d}.png")


def extract_textlayer_norm(book_page: int, manifest: list[dict]) -> str:
    from extract_textlayer import textlayer_page

    code, idx = locate(book_page, manifest)
    return textlayer_page(pymupdf.open(RAW / f"{code}.pdf")[idx])


METHODS = {
    "textlayer_norm+fractions": extract_textlayer_norm,
    "hybrid_ocr+textlayer": extract_hybrid,
    "textlayer_pypdf": extract_pypdf,
    "textlayer_pymupdf": extract_pymupdf,
    "textlayer_pdftotext": extract_pdftotext,
    "ocr_tesseract_hin": extract_tesseract,
    "ocr_tesseract_hin_eng": extract_tesseract_hin_eng,
    "vlm_qwen2.5vl": extract_vlm,
}


# ----------------------------------------------------------------------- scoring
def best_window(ref: str, hay: str) -> str:
    """Closest substring of `hay` to `ref`, so a method is not penalised for
    emitting blocks in a different order than the reference lines."""
    if not hay:
        return ""
    al = fuzz.partial_ratio_alignment(ref, hay, processor=None)
    return hay[al.dest_start : al.dest_end] if al else ""


def score_page(page: dict, text: str) -> dict:
    hay = norm(text)
    cer_num = cer_den = 0
    num_hit = num_tot = 0

    for line in page["prose"]:
        ref = norm(line)
        win = best_window(ref, hay)
        cer_num += Levenshtein.distance(ref, win)
        cer_den += len(ref)

        want = numbers(ref)
        got = numbers(win)
        num_tot += len(want)
        pool = list(got)
        for n in want:
            if n in pool:
                pool.remove(n)
                num_hit += 1

    fr_want = page.get("stacked_fractions", [])
    fr_hit = 0
    pool = hay
    for fr in fr_want:
        if fr in pool:
            fr_hit += 1

    return {
        "cer": cer_num / cer_den if cer_den else None,
        "numeric_recall": num_hit / num_tot if num_tot else None,
        "numbers_total": num_tot,
        "fraction_recall": fr_hit / len(fr_want) if fr_want else None,
        "fractions_total": len(fr_want),
        "chars_extracted": len(hay),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default=",".join(METHODS))
    ap.add_argument("--no-vlm", action="store_true")
    args = ap.parse_args()

    chosen = [m for m in args.methods.split(",") if m in METHODS]
    if args.no_vlm:
        chosen = [m for m in chosen if not m.startswith("vlm")]

    manifest = json.loads((ROOT / "ingest" / "manifest.json").read_text(encoding="utf-8"))
    pages = json.loads(GT.read_text(encoding="utf-8"))
    DUMP.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict] = {}
    for method in chosen:
        fn = METHODS[method]
        per_page, elapsed = {}, 0.0
        for page in pages:
            bp = page["book_page"]
            t0 = time.time()
            try:
                text = fn(bp, manifest)
            except Exception as exc:  # noqa: BLE001
                print(f"  {method} p{bp}: FAILED {exc}")
                text = ""
            elapsed += time.time() - t0
            (DUMP / f"p{bp:03d}.{method}.txt").write_text(text, encoding="utf-8")
            per_page[bp] = score_page(page, text)
            s = per_page[bp]
            print(
                f"  {method:22} p{bp:>3}  CER={s['cer']:.3f}  "
                f"num={s['numeric_recall'] if s['numeric_recall'] is None else f'{s['numeric_recall']:.2f}'}"
                f" ({s['numbers_total']})  frac="
                f"{'-' if s['fraction_recall'] is None else f'{s['fraction_recall']:.2f}'}"
            )

        cers = [p["cer"] for p in per_page.values() if p["cer"] is not None]
        nr = [(p["numeric_recall"], p["numbers_total"]) for p in per_page.values()
              if p["numeric_recall"] is not None]
        fr = [(p["fraction_recall"], p["fractions_total"]) for p in per_page.values()
              if p["fraction_recall"] is not None]
        results[method] = {
            "pages": per_page,
            "mean_cer": sum(cers) / len(cers) if cers else None,
            "numeric_recall": (sum(v * w for v, w in nr) / sum(w for _, w in nr)) if nr else None,
            "fraction_recall": (sum(v * w for v, w in fr) / sum(w for _, w in fr)) if fr else None,
            "sec_per_page": elapsed / len(pages),
        }
        r = results[method]
        print(
            f"  -> {method}: mean CER {r['mean_cer']:.3f}  numeric {r['numeric_recall']:.3f}  "
            f"fractions {'-' if r['fraction_recall'] is None else f'{r["fraction_recall"]:.3f}'}  "
            f"{r['sec_per_page']:.1f}s/page\n"
        )

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 78)
    print(f"{'method':24}{'prose CER':>11}{'numeric':>10}{'fractions':>11}{'s/page':>9}")
    print("-" * 78)
    for m, r in sorted(results.items(), key=lambda kv: kv[1]["mean_cer"]):
        fr_s = "-" if r["fraction_recall"] is None else f"{r['fraction_recall']:.1%}"
        print(
            f"{m:24}{r['mean_cer']:>10.1%}{r['numeric_recall']:>10.1%}{fr_s:>11}"
            f"{r['sec_per_page']:>8.1f}s"
        )
    print(f"\nfull results -> {OUT}\nraw text dumps -> {DUMP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
