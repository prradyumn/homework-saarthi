"""Hybrid page extraction: OCR prose + text-layer numerals + reconstructed fractions.

Why hybrid, from the measured bake-off (eval/textlayer_bakeoff.json):

  - The embedded text layer has broken Devanagari (Kokila subsets with bad
    ToUnicode maps): 15.6% prose CER, substituting भुजाओं -> िुजाओं, स्थान -> ्लथान.
  - Tesseract `hin` reads the Devanagari well (7.4% CER) but DESTROYS digits: its
    Devanagari model reads "1" as danda, so 150 -> 50, 100 -> 00, 1,000 -> ,000.
    It does not merely drop them, it emits a plausible wrong number. That is the
    §8.1 failure mode exactly: undetectable by the parent who asked.
  - The text layer's digits, by contrast, are Latin-encoded and exact: 97/97
    number tokens on p78, zero missing.
  - Nothing recovers stacked fractions, because 1/2 is a typographic layout
    (numerator, rule, denominator), not a character. But the rule is a drawn path
    and the digits carry coordinates, so fractions can be rebuilt geometrically.

So: take prose from OCR, and overwrite every digit-bearing OCR word with the
text-layer token at the same position. Deterministic, no model in the loop,
no hallucination surface.

Repairing the font CMap instead was tried and abandoned: aligning text-layer
against OCR output yields 197 distinct character-substitution pairs whose top 20
cover only 31%, because the corruption is conjunct-level rather than a 1:1
character permutation.
"""

from __future__ import annotations

import csv
import io
import pathlib
import re
import subprocess

import pymupdf

DPI = 300
PT_PER_PX = 72.0 / DPI  # tesseract works in render pixels, pymupdf in PDF points

# Real digits only. Tesseract renders "1" as danda (।), but danda is also ordinary
# Hindi sentence punctuation, so treating it as a digit candidate rewrites every
# full stop into a nearby number ("बनाता है।" -> "बनाता है150"). Instead, a "1" that
# OCR turned into punctuation is recovered by the unconsumed-number pass below.
DIGITISH = re.compile(r"[\d०-९]")
NUMERIC_TOKEN = re.compile(r"^[\d,./]+$")


def horizontal_rules(page: pymupdf.Page, max_width: float = 40.0) -> list[pymupdf.Rect]:
    """Short, thin, wide-vs-tall drawn paths — fraction bars. The width cap keeps
    table borders and underlines out; requiring digits above AND below (see
    fraction_groups) removes the rest."""
    out = []
    for d in page.get_drawings():
        r = d["rect"]
        if 3 < r.width <= max_width and r.height < 3:
            out.append(r)
    return out


def _digit_spans(page: pymupdf.Page) -> list[tuple[str, pymupdf.Rect]]:
    """Digit runs with a TIGHT bounding box, built from per-character boxes.

    Span-level boxes are useless for positional matching here: one span can be
    "35 ( 30 + 5 )", so every number inside it shares a single wide box and the
    overlap test picks an arbitrary one.
    """
    out: list[tuple[str, pymupdf.Rect]] = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                run, box = "", None
                for ch in span.get("chars", []):
                    c = ch["c"]
                    if c.isdigit() or (run and c in ",."):
                        run += c
                        r = pymupdf.Rect(ch["bbox"])
                        box = r if box is None else (box | r)
                    else:
                        if run and box is not None:
                            out.append((run.rstrip(",."), box))
                        run, box = "", None
                if run and box is not None:
                    out.append((run.rstrip(",."), box))
    return out


def fraction_groups(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    """Rebuild stacked fractions as 'n/d' from geometry: a fraction bar with
    x-overlapping digits centred above and below it."""
    spans = _digit_spans(page)
    found = []
    for rule in horizontal_rules(page):
        above, below = [], []
        for text, box in spans:
            digits = "".join(ch for ch in text if ch.isdigit())
            if not digits:
                continue
            # require horizontal overlap with the bar
            overlap = min(box.x1, rule.x1) - max(box.x0, rule.x0)
            if overlap <= 0.3 * min(box.width, rule.width):
                continue
            centre = (box.y0 + box.y1) / 2
            if centre < rule.y0:
                above.append((centre, digits, box))
            elif centre > rule.y0:
                below.append((centre, digits, box))
        if not above or not below:
            continue
        num = max(above, key=lambda t: t[0])   # nearest above the bar
        den = min(below, key=lambda t: t[0])   # nearest below
        span = pymupdf.Rect(rule)
        span.include_rect(num[2])
        span.include_rect(den[2])
        found.append((span, f"{num[1]}/{den[1]}"))
    return found


def textlayer_numbers(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    """Numeric tokens with their boxes, fractions first so a fraction's digits are
    not also offered individually."""
    tokens = list(fraction_groups(page))
    covered = [r for r, _ in tokens]
    for text, box in _digit_spans(page):
        if any(r.intersects(box) for r in covered):
            continue
        tokens.append((box, text))
    return tokens


def ocr_words(png: pathlib.Path, lang: str = "hin") -> list[dict]:
    """Word boxes from tesseract TSV, converted from render pixels to PDF points.

    Tesseract's own block/paragraph/line numbering is kept and used for grouping.
    Re-deriving lines from y-coordinates instead costs 27 points of prose CER on
    these pages, because banding by y merges side-by-side layout blocks.
    """
    proc = subprocess.run(
        ["tesseract", str(png), "stdout", "-l", lang, "--psm", "3", "tsv"],
        capture_output=True, text=True,
    )
    words = []
    for row in csv.DictReader(io.StringIO(proc.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        left, top = float(row["left"]) * PT_PER_PX, float(row["top"]) * PT_PER_PX
        w, h = float(row["width"]) * PT_PER_PX, float(row["height"]) * PT_PER_PX
        words.append(
            {
                "box": pymupdf.Rect(left, top, left + w, top + h),
                "text": row["text"].strip(),
                "conf": int(float(row["conf"])),
                "line": (int(row["block_num"]), int(row["par_num"]), int(row["line_num"])),
            }
        )
    return words


def hybrid_page(page: pymupdf.Page, png: pathlib.Path) -> str:
    """OCR words in reading order, with digit-bearing words replaced by the
    text-layer token that best overlaps them."""
    numbers = textlayer_numbers(page)
    used: set[int] = set()
    lines: dict[tuple[int, int, int], list[tuple[float, str]]] = {}
    line_pos: dict[tuple[int, int, int], tuple[float, float]] = {}

    for word in ocr_words(png):
        text, box = word["text"], word["box"]
        if word["conf"] < 30 and not DIGITISH.search(text):
            continue
        if DIGITISH.search(text):
            best, best_i, best_score = None, None, 0.0
            for i, (nbox, ntext) in enumerate(numbers):
                inter = pymupdf.Rect(box) & nbox
                if inter.is_empty:
                    continue
                score = inter.get_area() / max(box.get_area(), 1e-6)
                if score > best_score:
                    best, best_i, best_score = ntext, i, score
            if best is not None and best_score > 0.15:
                # a purely numeric OCR word is replaced outright; a mixed word
                # (digits glued to Devanagari) has its digit run substituted
                if NUMERIC_TOKEN.match(text) or best_i in used:
                    text = best
                else:
                    text = re.sub(r"[\d०-९।॥][\d,.०-९]*", best, text, count=1)
                used.add(best_i)
        key = word["line"]
        lines.setdefault(key, []).append((box.x0, text))
        if key not in line_pos:
            line_pos[key] = (box.y0, box.x0)

    # Every text-layer number the OCR never surfaced — a stacked fraction (read as
    # bare digits on two lines) or a "1" that OCR turned into a danda — is placed
    # into the nearest OCR line at its own x position, rather than dropped.
    for i, (nbox, ntext) in enumerate(numbers):
        if i in used:
            continue
        nearest = min(
            line_pos,
            key=lambda k: abs(line_pos[k][0] - nbox.y0) + 0.1 * abs(line_pos[k][1] - nbox.x0),
            default=None,
        )
        if nearest is None:
            continue
        if any(t == ntext for _, t in lines[nearest]):
            continue  # already present; do not duplicate
        lines[nearest].append((nbox.x0, ntext))

    ordered = sorted(lines, key=lambda k: (line_pos[k][0], line_pos[k][1]))
    return "\n".join(
        " ".join(t for _, t in sorted(lines[k], key=lambda p: p[0])) for k in ordered if lines[k]
    )
