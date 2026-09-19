"""Romanised Hindi must reach the same passage as its Devanagari twin.

Most Indian users type Roman script, and a parent on a low-cost Android often has
no Devanagari keyboard — so this is the ordinary path, not an edge case.

Before `translit.py`, measured on exactly these pairs: **0 of 5** reached the same
chunk, 3 of 5 were rejected as `no_maths_topic`, and 2 of 5 PASSED the gate on the
wrong chapter. That last pair is why this file exists: a rejection is safe, but a
confident answer drawn from an unrelated chapter is the failure §8.1 is built to
prevent, arriving through the front door.

Needs no LLM budget for the retrieval half; the transliteration half spends ~120
tokens per uncached query (about 5% of an answer).

    python scripts/test_hinglish.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, "scripts")

PAIRS = [
    ("ye concept batao bhinn ka",             "भिन्न क्या होती है?"),
    ("1 kilogram me kitne gram hote hain",    "1 किलोग्राम में कितने ग्राम होते हैं?"),
    ("tulya bhinn kya hoti hai",              "तुल्य भिन्न क्या होती है?"),
    ("naksha me jagah kaise dhundhte hain",   "नक्शे में जगह कैसे ढूँढ़ते हैं?"),
    ("sam panchbhuj se tile kyon nahi banti", "सम पंचभुज से टाइल क्यों नहीं बनती?"),
    ("mere bachche ko ginti nahi aati kaise sikhaun", "गिनती कैसे सिखाऊँ?"),
]

# Must NOT be treated as Hinglish: English keeps D15's behaviour, Devanagari is
# already correct, and spending a model call on either is pure waste.
NOT_HINGLISH = [
    "how many grams are in one kilogram?",
    "explain equivalent fractions to my child",
    "तुल्य भिन्न क्या होती है?",
    "1 किलोग्राम में कितने ग्राम होते हैं?",
]

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  — {detail}" if detail else ""))


def main() -> int:
    import translit
    from answer import retrieve
    from query_gate import pre_check

    print("\n  detection — a model call must only be spent on actual Hinglish\n")
    for t in NOT_HINGLISH:
        check(f"not flagged: {t[:44]}", not translit.looks_hinglish(t))
    for hin, _ in PAIRS:
        check(f"flagged: {hin[:44]}", translit.looks_hinglish(hin))

    print("\n  normalisation + retrieval\n")
    same_chapter = same_chunk = 0
    for hin, deva in PAIRS:
        norm = translit.to_devanagari(hin)
        check(f"transliterated: {hin[:34]}", norm["changed"], norm["text"][:44])
        if not norm["changed"]:
            continue

        # The gate must stop rejecting these as "not a maths question".
        g = pre_check(norm["text"])
        check(f"gate accepts: {hin[:34]}", g["outcome"] == "pass",
              f"{g['outcome']}/{g['reason']}")

        h_top, h_score = retrieve(norm["text"])[0][0]
        d_top, _ = retrieve(deva)[0][0]
        same_chapter += h_top["chapter"] == d_top["chapter"]
        same_chunk += h_top["id"] == d_top["id"]
        check(f"same chapter as Devanagari: {hin[:26]}",
              h_top["chapter"] == d_top["chapter"],
              f"ch{h_top['chapter']}p{h_top['page']} vs ch{d_top['chapter']}p{d_top['page']}")
        # A wrong-chapter answer is the dangerous outcome; a low score is merely
        # a refusal. Assert the floor that separates them.
        check(f"score clears the gate floor: {hin[:22]}", h_score >= 0.5, f"{h_score:.3f}")

    n = len(PAIRS)
    print(f"\n  chapter match {same_chapter}/{n}   exact chunk {same_chunk}/{n}")
    check("every Hinglish question reaches the right chapter", same_chapter == n,
          f"{same_chapter}/{n}")

    print(f"\n  {len(PASS)} passed, {len(FAIL)} failed\n")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
