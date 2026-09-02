"""Fetch NCERT Class 5 Maths (Hindi) — गणित मेला / Maths Mela, NCF-2023 edition.

Source of truth: ncert.nic.in. Nothing here is redistributed; the repo ships this
script and the derived chunk DB, never the textbook (pages carry a
"© NCERT / not to be republished" watermark).

Book page numbers come from the printed विषय-सूची (contents page, prelims p15 of
ehmm1ps.pdf). We assert PDF page counts against the gaps between chapter start
pages, which validates the pdf_page -> book_page mapping that FR-10 depends on:
a parent is told a page number they have to find in a physical book.
"""

import json
import pathlib
import subprocess
import sys

BASE = "https://ncert.nic.in/textbook/pdf"
BOOK_CODE = "ehmm1"  # e=primary, h=hindi, mm=Maths Mela, 1=class 5 book 1
RAW = pathlib.Path(__file__).resolve().parent.parent / "ingest" / "raw"

# (chapter_no, hindi_title, first printed page) — transcribed from the contents page.
CHAPTERS = [
    (1, "हम हैं यात्री – 1", 1),
    (2, "भिन्न", 17),
    (3, "घुमाव के रूप में कोण", 32),
    (4, "हम हैं यात्री – 2", 42),
    (5, "दूर और पास", 57),
    (6, "दुग्धशाला (डेयरी फार्म)", 70),
    (7, "आकार और प्रतिरूप", 92),
    (8, "भार और धारिता", 104),
    (9, "नारियल का खेत", 119),
    (10, "सममितीय अभिकल्पनाएँ", 136),
    (11, "दादी माँ की रजाई", 142),
    (12, "दौड़ते सेकंड", 155),
    (13, "जानवरों की छलाँग", 164),
    (14, "मानचित्र और अवस्थितियाँ", 171),
    (15, "चित्रों के माध्यम से आँकड़े", 179),
]
END_MATTER_PAGE = 191  # अधिगम सामग्री पत्रक — bounds chapter 15


def fetch(code: str, attempts: int = 4) -> pathlib.Path:
    """Download one PDF. ncert.nic.in resets TLS connections intermittently, so we
    shell out to curl and retry rather than trusting a single urllib call."""
    dest = RAW / f"{code}.pdf"
    if _complete(dest):
        return dest
    url = f"{BASE}/{code}.pdf"
    last = ""
    for _ in range(attempts):
        proc = subprocess.run(
            ["curl", "-sSL", "--max-time", "180", "--retry", "3", "-o", str(dest), url],
            capture_output=True,
            text=True,
        )
        if _complete(dest):
            return dest
        last = proc.stderr.strip() or f"truncated at {dest.stat().st_size if dest.exists() else 0} bytes"
    raise RuntimeError(f"could not fetch {url}: {last}")


def _complete(path: pathlib.Path) -> bool:
    """A PDF is only usable if it has both a %PDF header and a %%EOF trailer.
    Header-only checks pass on truncated downloads, which is how ch6 first
    arrived as a 192KB fragment that opened with zero pages."""
    if not path.exists() or path.stat().st_size < 10_000:
        return False
    blob = path.read_bytes()
    return blob[:4] == b"%PDF" and b"%%EOF" in blob[-2048:]


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    import pymupdf

    fetch(f"{BOOK_CODE}ps")  # prelims, carries the contents page
    manifest, failures = [], []

    starts = [c[2] for c in CHAPTERS] + [END_MATTER_PAGE]
    for i, (num, title, start) in enumerate(CHAPTERS):
        code = f"{BOOK_CODE}{num:02d}"
        try:
            path = fetch(code)
            pages = pymupdf.open(path).page_count
        except Exception as exc:  # noqa: BLE001
            failures.append(f"ch{num}: {exc}")
            continue

        # The last chapter's PDF also carries the end matter (अधिगम सामग्री पत्रक,
        # book pp 191-200): fraction-kit strips and cut-out polygons. They are
        # manipulatives with no explanatory prose, so they are recorded as a
        # separate section and excluded from the retrieval corpus.
        prose_pages = pages
        if num == CHAPTERS[-1][0]:
            prose_pages = END_MATTER_PAGE - start
            manifest.append(
                {
                    "chapter": None,
                    "section": "end_matter",
                    "title_hi": "अधिगम सामग्री पत्रक",
                    "code": code,
                    "pdf_pages": pages - prose_pages,
                    "pdf_page_first": prose_pages,
                    "book_page_start": END_MATTER_PAGE,
                    "book_page_end": start + pages - 1,
                    "page_offset": start,
                    "in_retrieval_corpus": False,
                }
            )

        expected = starts[i + 1] - start
        ok = prose_pages == expected
        flag = "ok " if ok else "MISMATCH"
        print(
            f"  ch{num:>2} {flag}  prose_pages={prose_pages:>2} expected={expected:>2}  "
            f"book_pp {start}-{start + prose_pages - 1}  {title}"
        )
        if not ok:
            failures.append(f"ch{num}: {prose_pages} prose pages but contents implies {expected}")

        manifest.append(
            {
                "chapter": num,
                "section": "chapter",
                "title_hi": title,
                "code": code,
                "pdf_pages": prose_pages,
                "pdf_page_first": 0,
                "book_page_start": start,
                "book_page_end": start + prose_pages - 1,
                "page_offset": start,  # book_page = page_offset + pdf_page_index (0-based)
                "in_retrieval_corpus": True,
            }
        )

    out = RAW.parent / "manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(m["pdf_pages"] for m in manifest)
    print(f"\n{len(manifest)}/15 chapters, {total} pages -> {out}")

    if failures:
        print("\nINTEGRITY FAILURES (page mapping is not trustworthy until resolved):")
        for f in failures:
            print("  -", f)
        return 1
    print("Page mapping verified: every chapter's PDF length matches the printed contents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
