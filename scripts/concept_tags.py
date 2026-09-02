"""Hindi keyword -> concept_tag vocabulary for NCERT Class 5 Maths (गणित मेला).

Tagging is deterministic keyword matching, not a model call. Three reasons:
free, auditable (you can see exactly why a chunk got a tag), and stable — a tag
that drifts between ingest runs would silently change what the §8.1 syllabus
check compares against.

Tags are `area.concept`, matching PRD Appendix B's example `fractions.equivalent`.
"""

from __future__ import annotations

import re

# Order matters: the most specific patterns are checked first, so
# "तुल्य भिन्न" wins over the bare "भिन्न".
TAG_VOCAB: list[tuple[str, str]] = [
    # fractions
    (r"तुल्य\s*भिन्न|समतुल्य", "fractions.equivalent"),
    (r"भिन्न\s*किट", "fractions.manipulatives"),
    (r"भिन्न", "fractions"),
    # place value and large numbers
    (r"स्थानीय\s*मान", "numbers.place_value"),
    (r"दस\s*हजार|लाख|हजार", "numbers.large_numbers"),
    (r"संख्या\s*रेखा", "numbers.number_line"),
    (r"पूर्वानुमान|आकलन", "numbers.estimation"),
    # the four operations
    (r"गुणनखंड|गुणज", "arithmetic.factors_multiples"),
    (r"गुणा|गुणन", "arithmetic.multiplication"),
    (r"भाग\s*दे|विभाजन|भाजक|भाज्य|भागफल|शेषफल", "arithmetic.division"),
    (r"जोड़|योग", "arithmetic.addition"),
    (r"घटा|व्यवकलन|अंतर\s*ज्ञात", "arithmetic.subtraction"),
    (r"दशमलव", "arithmetic.decimals"),
    # geometry
    (r"सममिति|सममितीय|दर्पण", "geometry.symmetry"),
    (r"घुमाव|कोण|समकोण", "geometry.angles"),
    (r"टाइल|प्रतिरूप|टैनग्राम", "geometry.tiling_patterns"),
    (r"त्रिभुज|चतुर्भुज|पंचभुज|षट्भुज|बहुभुज|वर्ग", "geometry.polygons"),
    (r"मानचित्र|अवस्थिति|दिशा", "geometry.maps_position"),
    (r"घन|घनाभ|त्रिविमीय", "geometry.3d_shapes"),
    # measurement
    (r"क्षेत्रफल", "measurement.area"),
    (r"परिमाप", "measurement.perimeter"),
    (r"धारिता|लीटर|मिलीलीटर", "measurement.capacity"),
    (r"भार|किलोग्राम|ग्राम|तौल", "measurement.weight"),
    (r"सेकंड|मिनट|घंटे|घंटा|समय|दिनचर्या", "measurement.time"),
    (r"दूरी|लंबाई|लंबाइयों|मीटर|सेंटीमीटर|किलोमीटर", "measurement.length"),
    (r"इकाई|इकाइयाँ|इकाइयों|मात्रक", "measurement.units"),
    (r"रुपये|रुपए|₹|मुद्रा|कीमत|बचत", "money"),
    # data
    (r"दंड\s*-?\s*आरेख", "data.bar_graph"),
    (r"चित्रालेख|चित्र\s*आरेख", "data.pictograph"),
    (r"आँकड़|आंकड़|तालिका", "data.handling"),
]

# Fallback per chapter, so every chunk carries at least a chapter-level tag even
# when its own text has no keyword hit.
CHAPTER_TAGS: dict[int, str] = {
    1: "numbers.large_numbers",
    2: "fractions",
    3: "geometry.angles",
    4: "numbers.large_numbers",
    5: "measurement.length",
    6: "arithmetic.multiplication",
    7: "geometry.tiling_patterns",
    8: "measurement.weight",
    9: "measurement.area",
    10: "geometry.symmetry",
    11: "geometry.tiling_patterns",
    12: "measurement.time",
    13: "measurement.length",
    14: "geometry.maps_position",
    15: "data.handling",
}

# Every pattern gets a Devanagari prefix guard, so a keyword only matches at the
# start of a word. Without it, substrings collide destructively in Hindi:
#   विभिन्न  ("various")  contains  भिन्न  ("fraction")   -> measurement tagged as fractions
#   टैनग्राम ("tangram")  contains  ग्राम   ("gram")       -> a puzzle tagged as weight
# Suffixes are still matched (भिन्नों, आँकड़ों) because only the prefix is guarded;
# Python's \b is useless here since \w includes Devanagari matras.
_GUARD = r"(?<![ऀ-ॿ])"
_COMPILED = [(re.compile(rf"{_GUARD}(?:{p})"), t) for p, t in TAG_VOCAB]


def tags_for(text: str, limit: int = 3) -> list[str]:
    """All tags whose keywords appear, most-hit first, most-specific as tiebreak."""
    scored: list[tuple[int, int, str]] = []
    for order, (rx, tag) in enumerate(_COMPILED):
        n = len(rx.findall(text))
        if n:
            scored.append((-n, order, tag))
    seen, out = set(), []
    for _n, _o, tag in sorted(scored):
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out[:limit]


def primary_tag(header: str, body: str, chapter: int) -> tuple[str, str]:
    """Returns (tag, source). A section header names its concept far more
    reliably than the body prose, which wanders through story context, so the
    header is tried first."""
    if header:
        hits = tags_for(header)
        if hits:
            return hits[0], "header"
    hits = tags_for(body)
    if hits:
        return hits[0], "body"
    return CHAPTER_TAGS.get(chapter, "unknown"), "chapter_fallback"
