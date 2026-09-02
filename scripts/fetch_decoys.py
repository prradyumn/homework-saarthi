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
            "needs_review": False,
            "figure_dependent": False,
            "unrepaired_conjuncts": 0,
            "chars": len(text),
        }
    )


if __name__ == "__main__":
    sys.exit(main())
