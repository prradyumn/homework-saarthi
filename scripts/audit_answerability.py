"""Audit the labelled set: is each "answer" question actually answerable from text?

D0.1 identified a refusal class the PRD does not name — questions whose answer
exists only in a figure. D3 then found a concrete case in my own labels:
"दर्पण जैसी आकृति कैसे बनाते हैं?" is labelled `answer`, but NO chunk anywhere in
the corpus contains दर्पण or सममित, because chapter 10 teaches symmetry entirely
through diagrams. The model declined, correctly, and was scored as a failure.

A labelled set that marks unanswerable questions as answerable makes every
downstream number wrong in the same direction: it depresses coverage and
conformance for reasons no amount of retrieval or prompt work can fix. So the
labels are audited against the corpus rather than trusted.

Test. Use CURATED topic vocabulary on both sides — the same Class 5 topic word
list the query gate uses, built from this book — and ask whether the topic words a
question uses also appear in its expected chapter.

Two automatic ways of picking the "key term" were tried first and both failed,
in opposite directions:

  - "any content word present" is far too lenient: "दर्पण जैसी आकृति कैसे बनाते
    हैं?" passes on the generic word आकृति while दर्पण, the word carrying the
    question, is absent from all 188k characters of the book.
  - "the rarest content word by IDF" is far too noisy: it selects verb
    inflections (घटाना, मापते, बदलें, निकालें), because the textbook conjugates
    differently than a parent speaks. It flagged 15 questions on verb forms alone.

Curated vocabulary avoids inferring which word matters: the list already encodes
it.

This is evidence, not proof: a concept can be present under different wording.
So the output is a candidate list for review, with the matched terms shown, and
the re-labelling decision is recorded in eval/refusal_set.py by hand.

Usage: python scripts/audit_answerability.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from query_gate import TOPIC_GROUPS  # noqa: E402

CHUNKS = ROOT / "ingest" / "chunks.json"
QSET = ROOT / "eval" / "refusal_set.json"
OUT = ROOT / "eval" / "answerability_audit.json"

TOPIC_VOCAB = sorted({w for ws in TOPIC_GROUPS.values() for w in ws})


def topic_words_in(text: str) -> list[str]:
    return [w for w in TOPIC_VOCAB
            if re.search(rf"(?<![ऀ-ॿ]){re.escape(w)}", text or "")]


def main() -> int:
    chunks = json.loads(CHUNKS.read_text(encoding="utf-8"))
    corpus = " ".join(
        f"{c['section_header_hi']} {c['chapter_title_hi']} {c['text_hi']}" for c in chunks
    )
    # chapter-level text too, so "present but in a different chapter" is visible
    by_chapter: dict[int, str] = defaultdict(str)
    for c in chunks:
        by_chapter[c["chapter"]] += f" {c['section_header_hi']} {c['text_hi']}"

    questions = [q for q in json.loads(QSET.read_text(encoding="utf-8"))
                 if q["label"] == "answer"]

    rows, no_vocab, absent_from_ch = [], [], []
    for q in questions:
        qw = topic_words_in(q["question_hi"])
        ch_text = by_chapter.get(q["expected_chapter"], "")
        in_ch = [w for w in qw
                 if re.search(rf"(?<![ऀ-ॿ]){re.escape(w)}", ch_text)]
        in_corpus = [w for w in qw
                     if re.search(rf"(?<![ऀ-ॿ]){re.escape(w)}", corpus)]
        row = {"id": q["id"], "chapter": q["expected_chapter"],
               "question_hi": q["question_hi"], "topic_words": qw,
               "in_expected_chapter": in_ch, "in_corpus": in_corpus}
        rows.append(row)
        if not qw:
            no_vocab.append(row)
        elif not in_ch:
            absent_from_ch.append(row)

    print(f"  audited {len(questions)} in-syllabus questions")
    print(f"  topic vocabulary: {len(TOPIC_VOCAB)} curated Class 5 words\n")

    print(f"  question uses NO curated topic word ({len(no_vocab)}):")
    for r in no_vocab:
        print(f"     ch{r['chapter']:>2}  {r['question_hi'][:56]}")

    print(f"\n  topic words present, but NONE in the expected chapter "
          f"({len(absent_from_ch)}):")
    for r in absent_from_ch:
        where = "nowhere in corpus" if not r["in_corpus"] else \
            f"elsewhere in corpus: {r['in_corpus']}"
        print(f"     ch{r['chapter']:>2}  {r['topic_words']}  ({where})")
        print(f"           {r['question_hi'][:60]}")

    per_ch: dict[int, list[bool]] = defaultdict(list)
    for r in rows:
        per_ch[r["chapter"]].append(bool(r["in_expected_chapter"]))
    print("\n  share of each chapter's questions whose words appear in that chapter:")
    for ch in sorted(per_ch):
        v = per_ch[ch]
        bar = "#" * round(10 * sum(v) / len(v))
        print(f"     ch{ch:>2}  {sum(v)}/{len(v):<3} {bar}")

    OUT.write_text(json.dumps({"n": len(rows), "no_topic_vocab": len(no_vocab),
                               "topic_absent_from_chapter": len(absent_from_ch),
                               "rows": rows}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n  -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
