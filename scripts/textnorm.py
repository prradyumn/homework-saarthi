"""Deterministic repair for the recoverable half of the text-layer corruption.

The broken Kokila ToUnicode maps damage Devanagari in two distinct ways:

1. **Duplicated combining marks and consonants** — recoverable, because the
   duplication carries no information:
       टाइल््स -> टाइल्स      भिन््न -> भिन्न
       व््यवस््थथित -> व्यवस्थित   दर््शशाते -> दर्शाते     मेें -> में
2. **A dropped consonant inside a conjunct** — NOT recoverable, because the
   character is simply absent:
       क्ैतिज -> क्षैतिज (ष gone)   यात्ा -> यात्रा (र gone)   द्ारा -> द्वारा (व gone)

So this is applied where the text layer is the chosen source — section headers and
callout labels, whose fonts survive well enough to be worth repairing — and never
relied on for body prose, which comes from OCR instead (see DECISIONS.md D0).
"""

from __future__ import annotations

import json
import pathlib
import re
import unicodedata

MARKS = r"[ऀ-ःऺ-ॏ॑-ॗॢॣ]"
CONS = r"[क-हक़-य़]"
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)

_DUP_MARK = re.compile(rf"({MARKS})\1+")
_DUP_CONS = re.compile(rf"({CONS})\1(?={MARKS})")
_DUP_CLUSTER = re.compile(rf"({CONS}{MARKS})\1+")
# A consonant doubled immediately after a halant is always an artifact: a real
# geminate is written with the halant between the pair (पक्का), never after it.
# This is what turns रिक्त into रिक्तत and वर्गों into वर्गगों.
_DUP_AFTER_HALANT = re.compile(rf"(्)({CONS})\2")
# A consonant doubled at the end of a word is also an artifact (समान -> समानन,
# विद्यालय -> विद्यालयय). Hindi writes real geminates with an intervening halant,
# so a bare doubled consonant with nothing after it is never legitimate.
_DUP_CONS_FINAL = re.compile(rf"({CONS})\1(?![ऀ-ॿ])")
# Signature of the unrecoverable class: a halant immediately followed by a
# dependent vowel means the conjunct's second consonant was never mapped.
_DROPPED = re.compile(r"्[ा-ौ]")


_REPAIR_FILE = pathlib.Path(__file__).resolve().parent.parent / "data" / "conjunct_repairs.json"


def _load_repairs() -> tuple[dict[str, str], re.Pattern | None]:
    if not _REPAIR_FILE.exists():
        return {}, None
    table = json.loads(_REPAIR_FILE.read_text(encoding="utf-8"))["repairs"]
    # Longest first, so क्ाओं is repaired before क्ा can match its prefix.
    keys = sorted(table, key=len, reverse=True)
    rx = re.compile(
        r"(?<![ऀ-ॿ])(" + "|".join(re.escape(k) for k in keys) + r")(?![ऀ-ॿ])"
    )
    return table, rx


REPAIRS, _REPAIR_RX = _load_repairs()


def repair_conjuncts(text: str) -> str:
    """Apply the hand-verified map for consonants the font never mapped.

    Only whole tokens are replaced, guarded on both sides against Devanagari, so
    a repair cannot fire inside an unrelated longer word.
    """
    if not _REPAIR_RX:
        return text
    return _REPAIR_RX.sub(lambda m: REPAIRS[m.group(1)], text)


def normalize(text: str, drop_zero_width: bool = True, repair: bool = True) -> str:
    text = unicodedata.normalize("NFC", text or "")
    if drop_zero_width:
        text = text.translate(ZERO_WIDTH)
    text = _DUP_CLUSTER.sub(r"\1", text)
    text = _DUP_MARK.sub(r"\1", text)
    text = _DUP_AFTER_HALANT.sub(r"\1\2", text)
    text = _DUP_CONS.sub(r"\1", text)
    text = _DUP_CONS_FINAL.sub(r"\1", text)
    if repair:
        text = repair_conjuncts(text)
    return re.sub(r"\s+", " ", text).strip()


_DUP_CONS_ANY = re.compile(rf"({CONS})\1")


def normalize_header(text: str) -> str:
    """Aggressive normalisation, for section headers and callout labels only.

    The decorative heading font duplicates consonants in positions the
    conservative rules deliberately leave alone — a doubled consonant followed by
    another consonant (प्रयत्न -> प्रययत्न, पवन -> पपवन, अथवा -> अथथवा).

    Collapsing every doubled consonant is NOT safe for body prose: Hindi has real
    words with adjacent identical consonants and no intervening halant (ममता),
    which the rule would corrupt. It is applied here because a header is used for
    segmentation and concept tagging, never quoted to a parent, and because
    leaving it uncollapsed breaks callout matching — "प्रययत्न कीजिए" fails to
    match the try_this pattern.
    """
    return _DUP_CONS_ANY.sub(r"\1", normalize(text))


def has_dropped_consonant(text: str) -> bool:
    """True if the text still carries the unrecoverable corruption signature, so a
    caller can flag the label as low-confidence rather than trust it."""
    return bool(_DROPPED.search(text))
