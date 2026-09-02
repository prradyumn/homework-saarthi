"""Detect a page's structural skeleton so chunks can follow the book's own units.

Fixed-size chunking is wrong for a textbook (PRD §11.3): the retrieval unit is a
concept unit — a worked example plus the explanation that introduces it. To cut
on concept boundaries you have to find them, and in this book they are typographic.

What does NOT work: font size. Body text is 17.0pt and section headings are
17-19pt, so a size threshold finds nothing.

What does work: the headings sit inside filled coloured pills. Measured over all
190 pages, the recurring filled boxes are:

    fill                 n    median height
    (0.00,0.00,0.00)   190    470   <- the "© NCERT not to be republished"
                                       watermark: every page, identical height
    (1.00,0.87,0.58)   100     29   <- amber section-header pill (~7 per chapter)
    (0.86,0.77,0.87)    59     34   <- lavender: header pills AND शिक्षण संकेत boxes
    (0.15,0.20,0.22)    46     70
    ...                             <- the rest are figure fills

Rather than hard-code those colours, a box is treated as a structural marker on
shape and content: pill-height, and containing a short line of words. That keeps
working if the next edition restyles the palette.
"""

from __future__ import annotations

import pathlib
import re

import pymupdf

from textnorm import has_dropped_consonant, normalize

# Header pills are one text line tall. Taller filled boxes with text are asides
# (teaching hints, activity panels); shorter ones are diagram fills.
PILL_MIN_H, PILL_MAX_H = 15.0, 46.0
ASIDE_MAX_H = 200.0
MIN_BOX_W = 55.0

WATERMARK_H = 470.0  # present on every page at exactly this height

# Callout kinds, matched against the OCR'd marker text. Tesseract is imperfect,
# so these match on a distinctive stem rather than the full phrase.
CALLOUT_PATTERNS = [
    ("teaching_hint", r"शिक्षण\s*संकेत"),
    ("activity", r"आइए\s*(पता|कर|देख|खेल|समझ)"),
    ("activity", r"मनोरंजन"),
    ("try_this", r"प्रयत्न\s*कीजिए"),
    ("discuss", r"चर्चा\s*कीजिए"),
    ("my_turn", r"मेरी\s*(दिनचर्या|बारी)"),
]


def _is_watermark(rect: pymupdf.Rect) -> bool:
    return abs(rect.height - WATERMARK_H) < 2.0


def filled_boxes(page: pymupdf.Page) -> list[pymupdf.Rect]:
    out = []
    for d in page.get_drawings():
        if not d.get("fill"):
            continue
        r = d["rect"]
        if r.width < MIN_BOX_W or r.height < PILL_MIN_H or _is_watermark(r):
            continue
        if r.height > ASIDE_MAX_H:
            continue
        out.append(r)
    return out


def markers(page: pymupdf.Page) -> list[dict]:
    """Structural markers on the page, in reading order.

    Marker text comes from the TEXT LAYER, not OCR — the inverse of body prose.
    The heading fonts defeat Tesseract completely: on p94 it read the pill
    "टाइल्स लगाना व उन्हें प्रतिरूप में व्यवस्थित करना" as
    "([ उकल्सलगना बड़नेंअतिलयमे सलस्थितकला )/" at confidence 0-11. The text layer
    renders the same pill as "टाइल््स लगाना व उन्हें प्रतिरूप मेें व््यवस््थथित करना",
    which the deterministic normaliser repairs.
    """
    found = []
    for box in filled_boxes(page):
        raw = page.get_textbox(box)
        text = normalize(raw)
        words = text.split()
        wide = box.width > 0.62 * page.rect.width  # table header rows span the column
        is_pill = box.height <= PILL_MAX_H and len(words) <= 10 and not wide
        # Pills hold a short heading; asides hold whole paragraphs. Capping both at
        # 16 words silently dropped every शिक्षण संकेत box — the teaching-hint
        # paragraphs written for teachers, which are the closest thing in the book
        # to "how to explain this" and so the most valuable content we have for
        # answer-contract parts 2 and 4.
        max_words = 10 if is_pill else 140
        if not (2 <= len(words) <= max_words):
            continue
        if len(re.findall(r"[ऀ-ॿ]+", text)) < 2:
            continue  # a number in a shaded diagram cell, not a heading

        kind = "section_header" if is_pill else "aside"
        for label, pattern in CALLOUT_PATTERNS:
            if re.search(pattern, text):
                kind = label if kind == "aside" else f"section_header:{label}"
                break
        found.append(
            {
                "y0": round(box.y0, 1), "y1": round(box.y1, 1),
                "x0": round(box.x0, 1), "x1": round(box.x1, 1),
                "kind": kind,
                "text": text,
                "height": round(box.height, 1),
                "label_uncertain": has_dropped_consonant(text),
            }
        )
    return sorted(found, key=lambda m: (m["y0"], m["x0"]))


def figure_regions(page: pymupdf.Page, min_area: float = 6000.0) -> list[pymupdf.Rect]:
    """Large image/graphic areas. Used to flag pages whose content is carried by a
    figure — the refusal class in DECISIONS.md D0.1, where no text extraction of
    any quality can answer the question."""
    out = []
    for img in page.get_images(full=True):
        for rect in page.get_image_rects(img[0]):
            if rect.get_area() >= min_area and not _is_watermark(rect):
                out.append(rect)
    for d in page.get_drawings():
        r = d["rect"]
        if d.get("fill") and r.get_area() >= min_area and not _is_watermark(r):
            out.append(r)
    return out
