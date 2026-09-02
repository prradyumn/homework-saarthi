"""Run the chosen hybrid extractor (D0) over all 190 prose pages.

Each page gets quality signals as well as text. There is no ground truth for 190
pages, so rather than assert an accuracy we record signals that correlate with
it and gate on them. A page that scores badly is marked `needs_review` and its
chunks are flagged low-confidence, so the retrieval gate can refuse on them
instead of answering from mush — §8.1's "a refusal is a product input" applied
one layer earlier, at ingest.

Output: ingest/corpus.json — one record per page.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

import pymupdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from extract_hybrid import hybrid_lines  # noqa: E402
from structure import figure_regions, markers  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "ingest" / "raw"
PAGES = ROOT / "ingest" / "pages"
OUT = ROOT / "ingest" / "corpus.json"

# Gate thresholds, set from the observed corpus distribution (printed below), not
# from intuition. Measured medians: OCR confidence 81.9, p05 56.8.
#
# A first version of this gate also flagged pages where many text-layer numbers
# had to be re-inserted by geometry. That flagged 55% of the book and was simply
# wrong: re-insertion is the mechanism working, because OCR deletes digits by
# construction (D0). The number-safety signal is `numbers_unverified` — digits
# OCR produced that NO text-layer number backs.
MIN_CONF_MEAN = 70.0
MAX_LOWCONF_SHARE = 0.45
MAX_UNVERIFIED = 6
MIN_PROSE_TOKENS = 20


def gate(stats: dict) -> tuple[bool, list[str]]:
    reasons = []
    if stats["ocr_conf_mean"] < MIN_CONF_MEAN:
        reasons.append(f"low OCR confidence ({stats['ocr_conf_mean']})")
    if stats["ocr_lowconf_share"] > MAX_LOWCONF_SHARE:
        reasons.append(f"{stats['ocr_lowconf_share']:.0%} of words below conf 60")
    if stats["numbers_unverified"] > MAX_UNVERIFIED:
        reasons.append(f"{stats['numbers_unverified']} unverified numbers in text")
    if stats["devanagari_tokens"] < MIN_PROSE_TOKENS:
        reasons.append(f"figure-only page ({stats['devanagari_tokens']} Devanagari tokens)")
    return (not reasons), reasons


def main() -> int:
    manifest = json.loads((ROOT / "ingest" / "manifest.json").read_text(encoding="utf-8"))
    records, t0 = [], time.time()

    for entry in manifest:
        if not entry["in_retrieval_corpus"]:
            continue
        doc = pymupdf.open(RAW / f"{entry['code']}.pdf")
        first = entry["pdf_page_first"]
        for idx in range(first, first + entry["pdf_pages"]):
            book_page = entry["page_offset"] + idx
            png = PAGES / f"p{book_page:03d}.png"
            page = doc[idx]
            lines, stats = hybrid_lines(page, png)
            page_markers = markers(page)
            figures = figure_regions(page)
            ok, reasons = gate(stats)
            records.append(
                {
                    "book_page": book_page,
                    "chapter": entry["chapter"],
                    "chapter_title_hi": entry["title_hi"],
                    "pdf_code": entry["code"],
                    "pdf_page_index": idx,
                    "lines": lines,
                    "markers": page_markers,
                    "figure_area_share": round(
                        sum(f.get_area() for f in figures) / page.rect.get_area(), 3
                    ),
                    "stats": stats,
                    "needs_review": not ok,
                    "review_reasons": reasons,
                }
            )
            print(
                f"  p{book_page:>3} ch{entry['chapter']:>2}  conf={stats['ocr_conf_mean']:>5.1f} "
                f"unver={stats['numbers_unverified']:>3} frac={stats['fractions_found']:>2} "
                f"mark={len(page_markers):>2} chars={stats['chars']:>5} "
                f"{'REVIEW: ' + '; '.join(reasons) if not ok else ''}",
                flush=True,
            )
        doc.close()

    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    flagged = [r for r in records if r["needs_review"]]
    confs = sorted(r["stats"]["ocr_conf_mean"] for r in records)

    def pct(p: float) -> float:
        return confs[min(int(p * len(confs)), len(confs) - 1)]

    print(f"\n{'=' * 78}")
    print(f"{len(records)} pages in {time.time() - t0:.0f}s -> {OUT}")
    print(
        f"OCR confidence  p05={pct(0.05):.1f}  p25={pct(0.25):.1f}  "
        f"median={pct(0.5):.1f}  p75={pct(0.75):.1f}  p95={pct(0.95):.1f}"
    )
    print(f"total chars {sum(r['stats']['chars'] for r in records):,}")
    print(f"fractions reconstructed {sum(r['stats']['fractions_found'] for r in records)}")
    print(f"markers found {sum(len(r['markers']) for r in records)}")
    print(
        f"unverified numbers {sum(r['stats']['numbers_unverified'] for r in records)} "
        f"of {sum(r['stats']['textlayer_numbers'] for r in records)} text-layer numbers"
    )
    print(f"\nflagged needs_review: {len(flagged)}/{len(records)} pages")
    for r in flagged:
        print(f"   p{r['book_page']:>3} ch{r['chapter']:>2}  {'; '.join(r['review_reasons'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
