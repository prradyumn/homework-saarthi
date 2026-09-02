"""Primary extractor: normalised text layer + geometrically rebuilt fractions.

This supersedes the OCR hybrid (see DECISIONS.md D0 REVISED). The short version:
the deterministic normaliser was written for section headers and never tried on
body prose, because by then OCR had already been chosen. Applied to the text
layer, it takes prose CER from 15.6% to **1.13%** with **100%** numeric recall —
seven times better than the OCR hybrid's 7.9%, with no OCR in the pipeline at all.

Sources, all from the PDF itself:
  prose      text layer, normalised (duplicated marks collapsed) and repaired
             (73-token hand-verified conjunct map in data/conjunct_repairs.json)
  numerals   text layer, Latin-encoded and exact
  fractions  geometry: a drawn fraction bar with digits above and below
  headers    text layer (scripts/structure.py)

Reading order comes from the PDF's own text layer, which is correct on these
pages — unlike the naive y-banding that cost 27 CER points when merging OCR.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pymupdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from extract_hybrid import fraction_groups_detailed, textlayer_numbers  # noqa: E402
from textnorm import has_dropped_consonant, normalize  # noqa: E402


def _covered(point: pymupdf.Point, rects: list[pymupdf.Rect]) -> bool:
    return any(r.contains(point) for r in rects)


def textlayer_lines(page: pymupdf.Page) -> tuple[list[dict], dict]:
    """Positioned, normalised lines with fractions spliced in at their x position."""
    fractions = fraction_groups_detailed(page)
    # the exact numerator/denominator boxes, not the enclosing fraction rect
    frects = [b for f in fractions for b in f["digit_boxes"]]

    # Lines are kept in the PDF's own block order and NEVER sorted by y. Sorting
    # globally by y interleaves side-by-side layout blocks and costs 2.9 CER
    # points (1.13% -> 4.05%) — the same mistake that cost 27 points when
    # merging OCR output. Character-level and span-level assembly measure
    # identically, so characters are used here to filter fraction digits exactly.
    lines: list[dict] = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            words: list[tuple[float, str]] = []
            buf, buf_x = "", None
            box: pymupdf.Rect | None = None
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    cbox = pymupdf.Rect(ch["bbox"])
                    centre = pymupdf.Point((cbox.x0 + cbox.x1) / 2, (cbox.y0 + cbox.y1) / 2)
                    # Only the digit itself is dropped, and only when its own box
                    # sits inside a fraction. Stripping every digit from any span a
                    # fraction merely touches destroyed real numbers elsewhere in
                    # the span ("35 ( 30 + 5 )"), taking numeric recall to 68.9%.
                    if ch["c"].isdigit() and _covered(centre, frects):
                        continue
                    box = cbox if box is None else (box | cbox)
                    if ch["c"].isspace():
                        if buf:
                            words.append((buf_x, buf))
                        buf, buf_x = "", None
                        continue
                    if not buf:
                        buf_x = cbox.x0
                    buf += ch["c"]
            if buf:
                words.append((buf_x, buf))
            if not words or box is None:
                continue
            lines.append({"words": words, "box": box})

    # Splice each fraction into the line it actually belongs to. Matching on y
    # alone dumped the pie-chart labels from p18's right margin into body lines
    # ("1/2 और 1/4 1/4 1/4"), so a candidate line must also be horizontally
    # adjacent. Fractions that match no line are figure labels, kept separately
    # rather than injected into prose (D0.1).
    figure_labels: list[str] = []
    for frac in fractions:
        frect, ftext = frac["rect"], frac["text"]
        centre = (frect.y0 + frect.y1) / 2
        candidates = [
            ln for ln in lines
            if ln["box"].y0 - 1 <= centre <= ln["box"].y1 + 1
            and ln["box"].x0 - 24 <= frect.x0 <= ln["box"].x1 + 48
        ]
        if candidates:
            min(candidates, key=lambda ln: abs(ln["box"].x0 - frect.x0))["words"].append(
                (frect.x0, ftext)
            )
        else:
            figure_labels.append(ftext)

    out = []
    for ln in lines:  # document order, deliberately not y-sorted
        text = normalize(" ".join(w for _x, w in sorted(ln["words"], key=lambda p: p[0])))
        if not text:
            continue
        out.append(
            {
                "text": text,
                "y0": round(ln["box"].y0, 1),
                "y1": round(ln["box"].y1, 1),
                "x0": round(ln["box"].x0, 1),
                "x1": round(ln["box"].x1, 1),
            }
        )

    joined = "\n".join(ln["text"] for ln in out)
    tokens = re.findall(r"[ऀ-ॿ]+", joined)
    unrepaired = [t for t in tokens if has_dropped_consonant(t)]
    numbers = textlayer_numbers(page)
    stats = {
        "chars": len(joined),
        "lines": len(out),
        "devanagari_tokens": len(tokens),
        "textlayer_numbers": len(numbers),
        "fractions_found": len(fractions),
        # tokens still carrying the unrecoverable signature: they are absent from
        # the repair map, so they are the ingest backlog for content-ops
        "unrepaired_conjuncts": len(unrepaired),
        "unrepaired_samples": sorted(set(unrepaired))[:8],
        "figure_label_fractions": figure_labels,
    }
    return out, stats


def textlayer_page(page: pymupdf.Page) -> str:
    return "\n".join(ln["text"] for ln in textlayer_lines(page)[0])
