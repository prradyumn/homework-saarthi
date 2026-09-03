"""Cut the corpus into concept units (PRD §11.3).

Fixed-size chunking is wrong for a textbook: the retrieval unit is a worked
example together with the explanation that introduces it. This book marks those
boundaries typographically — coloured header pills — so segmentation follows the
book's own structure rather than a character count (see scripts/structure.py).

Every chunk carries `class, subject, chapter, concept_tag, page`, because without
`class` on the chunk the §8.1 syllabus check ("is this even a Class 5 question?")
cannot run and the refusal logic collapses.

It also carries the flags the confidence gate needs to refuse honestly:

  needs_review          the page's extraction quality was below the gate
  unrepaired_conjuncts  tokens known to be missing a consonant the font never
                        mapped, i.e. words we know are wrong
  figure_dependent      the content is carried by a figure, so no text chunk can
                        answer it at any retrieval quality (DECISIONS.md D0.1)

Output: ingest/chunks.json
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from concept_tags import primary_tag, tags_for  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = ROOT / "ingest" / "corpus.json"
OUT = ROOT / "ingest" / "chunks.json"

CLASS, SUBJECT, LANG = 5, "maths", "hi"
BOOK = "NCERT गणित मेला (Maths Mela), Class 5, NCF-2023"

# A section is the natural unit, but some run long. Split those on line
# boundaries with the header repeated as context, and one line of overlap so a
# concept split across the seam is still retrievable from either side.
TARGET_MAX_CHARS = 1400
OVERLAP_LINES = 1
MIN_CHUNK_CHARS = 120

# A page that is mostly figure with little prose cannot be answered from text.
FIGURE_AREA_SHARE = 0.34
FIGURE_PROSE_TOKENS = 90


def is_header(marker: dict) -> bool:
    return marker["kind"].startswith("section_header")


def segment_chapter(pages: list[dict]) -> list[dict]:
    """Split a chapter's pages into sections at header pills, carrying sections
    across page breaks (a section usually spans ~2 pages)."""
    sections: list[dict] = []
    current = {"header": "", "header_kind": "", "lines": [], "pages": [], "asides": []}

    for page in pages:
        headers = sorted([m for m in page["markers"] if is_header(m)], key=lambda m: m["y0"])
        asides = [m for m in page["markers"] if not is_header(m)]
        bounds = [h["y0"] for h in headers]

        for line in page["lines"]:
            # a line that belongs to a header pill is the header itself, not body
            if any(h["y0"] - 3 <= line["y0"] <= h["y1"] + 3 for h in headers):
                continue
            crossed = [i for i, y in enumerate(bounds) if line["y0"] >= y - 3]
            if crossed:
                idx = crossed[-1]
                h = headers[idx]
                if current["header"] != h["text"] or not current["lines"]:
                    if current["lines"]:
                        sections.append(current)
                    current = {
                        "header": h["text"],
                        "header_kind": h["kind"],
                        "lines": [],
                        "pages": [],
                        "asides": [],
                    }
            current["lines"].append(line["text"])
            if page["book_page"] not in current["pages"]:
                current["pages"].append(page["book_page"])
            current.setdefault("page_meta", {})[page["book_page"]] = page

        for a in asides:
            current["asides"].append({"kind": a["kind"], "text": a["text"],
                                      "page": page["book_page"]})

    if current["lines"]:
        sections.append(current)
    return sections


def strip_page_numbers(text: str, pages: list[int]) -> str:
    """Remove the printed folio number, which sits in a coloured blob in the page
    corner and so arrives as a standalone number in the middle of the prose
    ("...होते हैं। 18 1/2 के समतुल्य..."). Only exact standalone matches for this
    chunk's own page numbers are removed, so real content numbers survive."""
    for p in pages:
        text = re.sub(rf"(?<![\d/]){p}(?![\d/])", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


# A run of four or more bare numbers with no words between them is a chart axis
# or a table header row, not prose. They arrive because the text layer contains
# every tick label ("8 7 6 5 4 1 0 ... 10 9" on p183).
#
# This matters twice over. They are noise in the chunk text, and they defeat the
# numeric-answerability check: a question asking for a value off a bar chart
# retrieves a chunk that LOOKS numeric while the actual data — the bar heights —
# exists only in the figure.
AXIS_RUN = re.compile(r"(?:(?<![\w/])\d{1,4}(?![\w/])[\s,]+){3,}(?<![\w/])\d{1,4}(?![\w/])")


def find_axis_runs(text: str) -> list[str]:
    return [m.group().strip() for m in AXIS_RUN.finditer(text)]


def split_long(lines: list[str]) -> list[list[str]]:
    parts, buf, size = [], [], 0
    for line in lines:
        if size + len(line) > TARGET_MAX_CHARS and buf:
            parts.append(buf)
            buf = buf[-OVERLAP_LINES:] if OVERLAP_LINES else []
            size = sum(len(x) for x in buf)
        buf.append(line)
        size += len(line)
    if buf:
        parts.append(buf)
    return parts


def main() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    by_chapter: dict[int, list[dict]] = {}
    for rec in corpus:
        by_chapter.setdefault(rec["chapter"], []).append(rec)
    for pages in by_chapter.values():
        pages.sort(key=lambda p: p["book_page"])

    chunks = []
    # part_no restarts at 0 for every SECTION, but several sections can begin on
    # the same page, so "ch02-p023-0" was issued three times. Everything
    # downstream keys chunks by id, which silently dropped 74 of 248 Class 5
    # chunks — 30% of the corpus was invisible to retrieval, and every metric
    # computed over "174 in-syllabus chunks" was computed over a truncated book.
    # The suffix is now a running sequence per (chapter, first page).
    seq: dict[tuple[int, int], int] = {}
    for chapter in sorted(by_chapter):
        pages = by_chapter[chapter]
        title = pages[0]["chapter_title_hi"]
        page_by_no = {p["book_page"]: p for p in pages}

        for section in segment_chapter(pages):
            for part_no, part in enumerate(split_long(section["lines"])):
                pgs = section["pages"]
                body = strip_page_numbers(" ".join(part).strip(), pgs)
                if len(body) < MIN_CHUNK_CHARS:
                    continue
                metas = [page_by_no[p] for p in pgs if p in page_by_no]
                header = section["header"]
                tag, tag_source = primary_tag(header, body, chapter)

                unrepaired = sum(m["stats"]["unrepaired_conjuncts"] for m in metas)
                review = [m["book_page"] for m in metas if m["needs_review"]]
                fig = [
                    m["book_page"] for m in metas
                    if m["figure_area_share"] >= FIGURE_AREA_SHARE
                    and m["stats"]["devanagari_tokens"] < FIGURE_PROSE_TOKENS
                ]

                key = (chapter, pgs[0])
                n = seq[key] = seq.get(key, -1) + 1
                chunks.append(
                    {
                        "id": f"c5-maths-hi-ch{chapter:02d}-p{pgs[0]:03d}-{n}",
                        # --- metadata the §8.1 syllabus check depends on ---
                        "class": CLASS,
                        "subject": SUBJECT,
                        "language": LANG,
                        "book": BOOK,
                        "chapter": chapter,
                        "chapter_title_hi": title,
                        "page": pgs[0],
                        "pages": pgs,
                        "concept_tag": tag,
                        "concept_tag_source": tag_source,
                        "concept_tags_all": tags_for(f"{header} {body}"),
                        # --- content ---
                        "section_header_hi": header,
                        "section_kind": section["header_kind"] or "chapter_body",
                        "text_hi": body,
                        "asides": [a for a in section["asides"] if part_no == 0],
                        # --- confidence flags for the retrieval gate ---
                        "needs_review": bool(review),
                        "review_pages": review,
                        "unrepaired_conjuncts": unrepaired,
                        "chart_axis_runs": find_axis_runs(body),
                        "has_chart_axis": bool(find_axis_runs(body)),
                        "fractions": sorted({
                            w for w in body.split() if "/" in w and any(c.isdigit() for c in w)
                        }),
                        "figure_dependent": bool(fig),
                        "chars": len(body),
                    }
                )

    # Fail loudly rather than let a downstream dict silently swallow a collision.
    ids = [c["id"] for c in chunks]
    if len(ids) != len(set(ids)):
        import collections
        dupes = {i: n for i, n in collections.Counter(ids).items() if n > 1}
        raise SystemExit(
            f"chunk id collision: {len(ids)} chunks, {len(set(ids))} distinct ids.\n"
            f"  {dupes}\n"
            f"  Every consumer keys chunks by id, so a collision drops chunks "
            f"without any error."
        )

    OUT.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")

    sizes = sorted(c["chars"] for c in chunks)
    q = lambda p: sizes[min(int(p * len(sizes)), len(sizes) - 1)]  # noqa: E731
    print(f"{len(chunks)} chunks -> {OUT}")
    print(f"chars: min={sizes[0]} p25={q(.25)} median={q(.5)} p75={q(.75)} max={sizes[-1]}")
    print(f"per chapter: {dict(sorted(Counter(c['chapter'] for c in chunks).items()))}")
    print(f"\ntag source: {dict(Counter(c['concept_tag_source'] for c in chunks))}")
    print(f"needs_review: {sum(c['needs_review'] for c in chunks)}")
    print(f"figure_dependent: {sum(c['figure_dependent'] for c in chunks)}")
    print(f"with unrepaired conjuncts: {sum(c['unrepaired_conjuncts'] > 0 for c in chunks)}")
    print(f"with inline fractions: {sum(bool(c['fractions']) for c in chunks)}")
    print(f"with chart/table axis runs: {sum(c['has_chart_axis'] for c in chunks)}")
    print("\ntop concept tags:")
    for tag, n in Counter(c["concept_tag"] for c in chunks).most_common(14):
        print(f"   {n:>3}  {tag}")
    unknown = [c for c in chunks if c["concept_tag"] == "unknown"]
    if unknown:
        print(f"\nUNTAGGED: {len(unknown)} chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
