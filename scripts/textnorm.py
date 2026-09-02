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

import re
import unicodedata

MARKS = r"[ऀ-ःऺ-ॏ॑-ॗॢॣ]"
CONS = r"[क-हक़-य़]"
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)

_DUP_MARK = re.compile(rf"({MARKS})\1+")
_DUP_CONS = re.compile(rf"({CONS})\1(?={MARKS})")
_DUP_CLUSTER = re.compile(rf"({CONS}{MARKS})\1+")
# Signature of the unrecoverable class: a halant immediately followed by a
# dependent vowel means the conjunct's second consonant was never mapped.
_DROPPED = re.compile(r"्[ा-ौ]")


def normalize(text: str, drop_zero_width: bool = True) -> str:
    text = unicodedata.normalize("NFC", text or "")
    if drop_zero_width:
        text = text.translate(ZERO_WIDTH)
    text = _DUP_CLUSTER.sub(r"\1", text)
    text = _DUP_MARK.sub(r"\1", text)
    text = _DUP_CONS.sub(r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def has_dropped_consonant(text: str) -> bool:
    """True if the text still carries the unrecoverable corruption signature, so a
    caller can flag the label as low-confidence rather than trust it."""
    return bool(_DROPPED.search(text))
