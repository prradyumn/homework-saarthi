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
from extract_textlayer import textlayer_lines  # noqa: E402
from structure import figure_regions, markers  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "ingest" / "raw"
PAGES = ROOT / "ingest" / "pages"
OUT = ROOT / "ingest" / "corpus.json"

# Gate thresholds. Two earlier versions of this gate measured the wrong thing:
#
#   v1 flagged pages where many numbers were re-inserted geometrically -> 55% of
#      the book, because that was the mechanism working, not failing.
#   v2 gated on OCR confidence, which stopped meaning anything once the text
#      layer replaced OCR as the prose source (D0 REVISED).
#
# The signal now is `unrepaired_conjuncts`: tokens still carrying the
# unrecoverable dropped-consonant signature, i.e. words we KNOW are wrong because
# they are missing from data/conjunct_repairs.json. It is exact rather than
# probabilistic, and it doubles as the content-ops backlog.
MAX_UNREPAIRED = 2
MIN_PROSE_TOKENS = 20


def gate(stats: dict) -> tuple[bool, list[str]]:
    reasons = []
    if stats["unrepaired_conjuncts"] > MAX_UNREPAIRED:
        reasons.append(
            f"{stats['unrepaired_conjuncts']} unrepaired conjuncts "
            f"({', '.join(stats['unrepaired_samples'][:4])})"
        )
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
            lines, stats = textlayer_lines(page)
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
                f"  p{book_page:>3} ch{entry['chapter']:>2}  nums={stats['textlayer_numbers']:>3} "
                f"frac={stats['fractions_found']:>2} mark={len(page_markers):>2} "
                f"tok={stats['devanagari_tokens']:>4} chars={stats['chars']:>5} "
                f"{'REVIEW: ' + '; '.join(reasons) if not ok else ''}",
                flush=True,
            )
        doc.close()

    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    flagged = [r for r in records if r["needs_review"]]
    print(f"\n{'=' * 78}")
    print(f"{len(records)} pages in {time.time() - t0:.0f}s -> {OUT}")
    print(f"total chars {sum(r['stats']['chars'] for r in records):,}")
    print(f"fractions reconstructed {sum(r['stats']['fractions_found'] for r in records)}")
    print(f"markers found {sum(len(r['markers']) for r in records)}")
    print(
        f"unrepaired conjuncts {sum(r['stats']['unrepaired_conjuncts'] for r in records)} "
        f"across {sum(r['stats']['devanagari_tokens'] for r in records):,} Devanagari tokens"
    )
    print(f"\nflagged needs_review: {len(flagged)}/{len(records)} pages")
    for r in flagged:
        print(f"   p{r['book_page']:>3} ch{r['chapter']:>2}  {'; '.join(r['review_reasons'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
