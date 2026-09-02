"""Fetch a small out-of-syllabus decoy corpus (Class 6-9 Maths, Hindi).

Why decoys exist (DECISIONS.md D1-PRELIM): PRD §8.1's signal 3 is "whether
retrieved chunk metadata matches Class 5 Maths" — but that check can never fire
when every chunk in the index IS Class 5 Maths. It contributes nothing.

Deliberately indexing higher-class content, tagged with its real class, makes the
check real: a Class 9 algebra question retrieves a chunk whose metadata says
`class: 9`, and the gate refuses on a label rather than guessing from a
similarity score. No model, no threshold, one extra ingest run.

Class 6, 7 and 8 matter more than 9 here: they are the NEAREST out-of-syllabus
content and therefore the hardest to distinguish. A gate that only rejects
trigonometry is not doing much.

Editions available in Hindi with a usable Unicode text layer:
  class 6  fhgp1  गणित प्रकाश (NCF)     class 8  hhgp1  (NCF)
  class 7  ghgp1  (NCF)                 class 9  ihmh1  (old edition)
Class 8-old (hhmh1) and Class 10 (jhmh1) Hindi are legacy non-Unicode fonts and
are unusable — but that does not matter, because a decoy is never answered from.

Decoys are chunked by size, not by concept unit: their internal structure is
irrelevant since they exist only to be recognised and refused.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import pymupdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from extract_textlayer import textlayer_lines  # noqa: E402

BASE = "https://ncert.nic.in/textbook/pdf"
ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "ingest" / "decoy_raw"
OUT = ROOT / "ingest" / "decoy_chunks.json"

# (class, code, book title, chapters to fetch)
DECOY_BOOKS = [
    (6, "fhgp1", "NCERT गणित प्रकाश, Class 6", range(1, 8)),
    (7, "ghgp1", "NCERT गणित, Class 7", range(1, 8)),
    (8, "hhgp1", "NCERT गणित, Class 8", range(1, 8)),
    (9, "ihmh1", "NCERT गणित, Class 9", range(1, 8)),
]

CHUNK_CHARS = 800
MIN_CHARS = 200

# "Out-of-syllabus" is a property of the TOPIC, not of the book's class label.
# Indexing whole higher-class books as decoys was a conceptual error: Class 6-8
# maths REVISITS fractions, angles, large numbers and area, so a Class 7
# fractions chapter is not out-of-syllabus for a fractions question — it is the
# same concept taught later. Wholesale decoys captured 41 of 100 legitimate
# parent questions as top-1, forcing the gate to refuse 76% of real questions to
# stay inside the 2% wrong-answer budget.
#
# So a decoy chunk is kept only if it mentions a topic VERIFIED ABSENT from the
# Class 5 corpus. Each marker below was checked for zero occurrences in
# ingest/chunks.json; near-misses were dropped for exactly this reason —
# गुणनखंड (31 hits), क्षेत्रफल (53), चर (60), पूर्णांक (1) are all Class 5
# vocabulary, and गुणनखंड is precisely why a Class 9 algebra question scored 0.569
# against the Class 5 corpus in the D1-PRELIM probe.
BEYOND_CLASS5 = [
    "बीजीय", "समीकरण", "व्यंजक", "सर्वसमिका",       # algebra
    "घातांक", "घात", "वर्गमूल", "घनमूल",             # exponents and roots
    "प्रतिशत", "अनुपात", "समानुपात",                 # ratio, percentage
    "परिमेय", "अपरिमेय", "दशमलव",                    # number systems
    "निर्देशांक", "सर्वांगसम", "प्रमेय",              # coordinate geometry, proofs
    "प्रायिकता", "माध्यिका",                          # probability, statistics
]
_BEYOND_RX = re.compile(r"(?<![ऀ-ॿ])(?:" + "|".join(BEYOND_CLASS5) + r")")

# Presence of a beyond-Class-5 marker is NOT enough. A Class 7 chapter on large
# numbers mentions दशमलव once in passing and then teaches column addition — a
# Class 5 topic — so the chunk passed the filter and then outranked the Class 5
# corpus on questions like "बड़ी संख्याओं को जोड़ने में हासिल कैसे लगाते हैं?"
# and "सम और विषम संख्या में क्या फर्क है?". Higher-class books cover Class 5
# topics at greater length, so they win on similarity.
#
# So a decoy must be PREDOMINANTLY beyond Class 5: it needs real beyond-syllabus
# density, and it must not be mostly Class 5 vocabulary. The Class 5 topic list
# is imported from the query gate so there is one definition of "Class 5 topic".
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from query_gate import TOPIC_WORDS as CLASS5_TOPIC_WORDS  # noqa: E402

_C5_RX = re.compile(r"(?<![ऀ-ॿ])(?:" + "|".join(CLASS5_TOPIC_WORDS) + r")")

# Kept deliberately permissive at INGEST time, with the density signals recorded
# on every chunk, so the filter can be swept during calibration from a single
# embedding run. A per-setting re-embed costs ~3 minutes; sweeping at scoring
# time costs nothing and lets the trade-off be seen whole.
MIN_BEYOND_HITS = 1
MAX_C5_RATIO = 0.0


def is_out_of_syllabus(text: str) -> tuple[bool, int, int]:
    beyond = len(_BEYOND_RX.findall(text))
    c5 = len(_C5_RX.findall(text))
    ok = beyond >= MIN_BEYOND_HITS and beyond >= c5 * MAX_C5_RATIO
    return ok, beyond, c5


def fetch(code: str, chapter: int, attempts: int = 3) -> pathlib.Path | None:
    dest = RAW / f"{code}{chapter:02d}.pdf"
    if _complete(dest):
        return dest
    url = f"{BASE}/{code}{chapter:02d}.pdf"
    for _ in range(attempts):
        subprocess.run(
            ["curl", "-sSL", "--max-time", "150", "--retry", "2", "-o", str(dest), url],
            capture_output=True, text=True,
        )
        if _complete(dest):
            return dest
    dest.unlink(missing_ok=True)
    return None


def _complete(path: pathlib.Path) -> bool:
    if not path.exists() or path.stat().st_size < 10_000:
        return False
    blob = path.read_bytes()
    return blob[:4] == b"%PDF" and b"%%EOF" in blob[-2048:]


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    chunks = []

    for klass, code, title, chapters in DECOY_BOOKS:
        got = 0
        for ch in chapters:
            path = fetch(code, ch)
            if path is None:
                continue
            doc = pymupdf.open(path)
            for idx in range(doc.page_count):
                lines, _stats = textlayer_lines(doc[idx])
                buf: list[str] = []
                size = 0
                for ln in lines:
                    buf.append(ln["text"])
                    size += len(ln["text"])
                    if size >= CHUNK_CHARS:
                        _emit(chunks, klass, code, title, ch, idx, buf)
                        buf, size = [], 0
                if size >= MIN_CHARS:
                    _emit(chunks, klass, code, title, ch, idx, buf)
            doc.close()
            got += 1
        print(f"  class {klass}: {got} chapters -> {sum(1 for c in chunks if c['class'] == klass)} chunks")

    OUT.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(chunks)} decoy chunks -> {OUT}")
    return 0


def _emit(chunks, klass, code, title, chapter, page_idx, buf) -> None:
    text = " ".join(buf).strip()
    if len(text) < MIN_CHARS:
        return
    ok, beyond, c5 = is_out_of_syllabus(text)
    if not ok:
        return  # a Class 5 topic taught later; not a valid decoy
    chunks.append(
        {
            "id": f"decoy-c{klass}-{code}-ch{chapter:02d}-p{page_idx:02d}-{len(chunks)}",
            "class": klass,
            "subject": "maths",
            "language": "hi",
            "book": title,
            "chapter": chapter,
            "chapter_title_hi": "",
            "page": page_idx + 1,
            "pages": [page_idx + 1],
            "concept_tag": "out_of_syllabus",
            "section_header_hi": "",
            "section_kind": "decoy",
            "text_hi": text,
            "fractions": [],
            "in_syllabus": False,
            "beyond_hits": beyond,
            "class5_hits": c5,
            "needs_review": False,
            "figure_dependent": False,
            "unrepaired_conjuncts": 0,
            "chars": len(text),
        }
    )


if __name__ == "__main__":
    sys.exit(main())
