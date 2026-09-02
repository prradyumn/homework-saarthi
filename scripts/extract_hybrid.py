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


OCR_CACHE = pathlib.Path(__file__).resolve().parent.parent / "ingest" / "ocr_cache"


def ocr_words(png: pathlib.Path, lang: str = "hin") -> list[dict]:
    """Word boxes from tesseract TSV, converted from render pixels to PDF points.

    Tesseract's own block/paragraph/line numbering is kept and used for grouping.
    Re-deriving lines from y-coordinates instead costs 27 points of prose CER on
    these pages, because banding by y merges side-by-side layout blocks.
    """
    # OCR is the slow step (~2s/page, 190 pages). Cache the TSV so that iterating
    # on chunking and segmentation does not re-run the whole corpus each time.
    cached = OCR_CACHE / f"{png.stem}.{lang}.tsv"
    if cached.exists():
        tsv = cached.read_text(encoding="utf-8")
    else:
        proc = subprocess.run(
            ["tesseract", str(png), "stdout", "-l", lang, "--psm", "3", "tsv"],
            capture_output=True, text=True,
        )
        tsv = proc.stdout
        OCR_CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_text(tsv, encoding="utf-8")

    words = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
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


MEANINGFUL = re.compile(r"[ऀ-ॿ0-9A-Za-z]")


def _is_junk(text: str, conf: int) -> bool:
    """OCR debris from diagram strokes and fraction bars, which the book has a lot
    of. On p18 it produced a stray "था" beside each fraction, plus "न * ठ" and
    "तर". Short low-confidence tokens and pure punctuation are dropped; the
    length limit keeps real short words (व, न, है) whenever OCR is confident.
    """
    if not MEANINGFUL.search(text):
        return True
    if conf < 30:
        return True
    return len(text) <= 2 and conf < 55


def hybrid_page(page: pymupdf.Page, png: pathlib.Path) -> str:
    return hybrid_page_with_stats(page, png)[0]


def hybrid_lines(page: pymupdf.Page, png: pathlib.Path) -> tuple[list[dict], dict]:
    """Positioned lines rather than a flat blob, so segmentation can cut on the
    book's own structure (see scripts/structure.py)."""
    return hybrid_page_with_stats(page, png, as_lines=True)


def hybrid_page_with_stats(
    page: pymupdf.Page, png: pathlib.Path, as_lines: bool = False
) -> tuple[str | list[dict], dict]:
    """OCR words in reading order, with digit-bearing words replaced by the
    text-layer token that best overlaps them.

    The stats are the per-page quality gate. There is no ground truth for 190
    pages, so instead of guessing at accuracy we record signals that correlate
    with it — OCR confidence, and how many text-layer numbers the OCR failed to
    surface at all. Pages that score badly become known content gaps that refuse
    (§8.1), rather than pages that answer badly.
    """
    numbers = textlayer_numbers(page)
    used: set[int] = set()
    lines: dict[tuple[int, int, int], list[tuple[float, str]]] = {}
    line_pos: dict[tuple[int, int, int], tuple[float, float]] = {}
    line_box: dict[tuple[int, int, int], pymupdf.Rect] = {}
    confs: list[int] = []
    dropped = substituted = unverified = 0

    for word in ocr_words(png):
        text, box = word["text"], word["box"]
        confs.append(word["conf"])
        if not DIGITISH.search(text) and _is_junk(text, word["conf"]):
            dropped += 1
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
                substituted += 1
            else:
                # A digit OCR read that no text-layer number backs — the
                # dangerous residue, and how "10 ( 2 × 5 )" becomes "225".
                # The text layer's digits are ground truth (97/97 on p78) and
                # every real number is already placed by the re-insertion pass
                # below, so an unbacked digit is junk or a duplicate. Drop it:
                # a missing number is recoverable, a wrong one is not (D0).
                unverified += 1
                stripped = re.sub(r"[\d०-९][\d,.०-९]*", "", text).strip()
                if not re.search(r"[ऀ-ॿ]", stripped):
                    continue  # nothing but the bogus digits; drop the word
                text = stripped
        key = word["line"]
        lines.setdefault(key, []).append((box.x0, text))
        if key not in line_pos:
            line_pos[key] = (box.y0, box.x0)
        line_box[key] = box if key not in line_box else (line_box[key] | box)

    # Every text-layer number the OCR never surfaced — a stacked fraction (read as
    # bare digits on two lines) or a "1" that OCR turned into a danda — is placed
    # into the nearest OCR line at its own x position, rather than dropped.
    for i, (nbox, ntext) in enumerate(numbers):
        if i in used:
            continue
        # Place it in the line whose vertical span actually overlaps the number,
        # falling back to the vertically nearest line. Scoring |dy| + 0.1|dx|
        # instead put p18's "1/5" into the line above the one it belongs to,
        # because a nearer line start won on x while being wrong on y.
        centre = (nbox.y0 + nbox.y1) / 2
        overlapping = [
            k for k in line_box
            if line_box[k].y0 - 1 <= centre <= line_box[k].y1 + 1
        ]
        if overlapping:
            nearest = min(overlapping, key=lambda k: abs(line_box[k].x0 - nbox.x0))
        else:
            nearest = min(
                line_box,
                key=lambda k: abs((line_box[k].y0 + line_box[k].y1) / 2 - centre),
                default=None,
            )
        if nearest is None:
            continue
        if any(t == ntext for _, t in lines[nearest]):
            continue  # already present; do not duplicate
        lines[nearest].append((nbox.x0, ntext))

    ordered = [k for k in sorted(lines, key=lambda k: (line_pos[k][0], line_pos[k][1])) if lines[k]]
    out_lines = [
        {
            "text": " ".join(t for _, t in sorted(lines[k], key=lambda p: p[0])),
            "y0": round(line_box[k].y0, 1),
            "y1": round(line_box[k].y1, 1),
            "x0": round(line_box[k].x0, 1),
            "x1": round(line_box[k].x1, 1),
        }
        for k in ordered
        if k in line_box
    ]
    text = "\n".join(ln["text"] for ln in out_lines)

    unmatched = [t for i, (_b, t) in enumerate(numbers) if i not in used]
    conf_sorted = sorted(confs)
    stats = {
        "ocr_words": len(confs),
        "ocr_conf_mean": round(sum(confs) / len(confs), 1) if confs else 0.0,
        "ocr_conf_p25": conf_sorted[len(conf_sorted) // 4] if conf_sorted else 0,
        "ocr_lowconf_share": round(sum(1 for c in confs if c < 60) / len(confs), 3) if confs else 1.0,
        "ocr_words_dropped": dropped,
        "textlayer_numbers": len(numbers),
        "numbers_substituted": substituted,
        # Numbers present in the PDF that OCR never surfaced at a matching
        # position, re-inserted here by geometry. NOT a quality signal: a high
        # count means the mechanism is doing its job, since OCR reads digits
        # badly by construction (it deletes every "1").
        "numbers_reinserted": len(unmatched),
        # THE safety signal: digit-bearing OCR words left in the text with no
        # text-layer number backing them. These may be invented.
        "numbers_unverified": unverified,
        "fractions_found": sum(1 for _b, t in numbers if "/" in t),
        "devanagari_tokens": len(re.findall(r"[ऀ-ॿ]+", text)),
        "chars": len(text),
    }
    return (out_lines if as_lines else text), stats
