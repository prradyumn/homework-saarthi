"""Render every prose page to PNG, named by its printed book page number.

Two consumers, one artifact:
  - the OCR paths in the text-layer bake-off (scripts/textlayer_bakeoff.py)
  - the refusal package and FR-10 ("show me the textbook page"), where the
    parent is shown the page image for the page number we quote

300 dpi because these are vector-source renders, not scans: this is the
best-case input an OCR engine can get, so a poor OCR result here would be the
engine's ceiling rather than an artifact of image quality.
"""

import json
import pathlib
import sys

import pymupdf

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "ingest" / "raw"
PAGES = ROOT / "ingest" / "pages"
DPI = 300


def main() -> int:
    manifest = json.loads((ROOT / "ingest" / "manifest.json").read_text(encoding="utf-8"))
    PAGES.mkdir(parents=True, exist_ok=True)
    written = 0

    for entry in manifest:
        if not entry["in_retrieval_corpus"]:
            continue
        doc = pymupdf.open(RAW / f"{entry['code']}.pdf")
        first = entry["pdf_page_first"]
        for idx in range(first, first + entry["pdf_pages"]):
            book_page = entry["page_offset"] + idx
            out = PAGES / f"p{book_page:03d}.png"
            if not out.exists():
                doc[idx].get_pixmap(dpi=DPI).save(out)
            written += 1
        doc.close()

    sizes = [p.stat().st_size for p in PAGES.glob("p*.png")]
    print(f"{written} pages at {DPI}dpi -> {PAGES}")
    print(f"total {sum(sizes) / 1e6:.0f} MB, mean {sum(sizes) / len(sizes) / 1e6:.1f} MB/page")
    return 0


if __name__ == "__main__":
    sys.exit(main())
