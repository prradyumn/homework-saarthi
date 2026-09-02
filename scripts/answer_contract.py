"""The four-part answer contract, and a validator that enforces it (PRD §10).

Every successful answer has exactly four parts, in this order:

  1  the answer                one sentence
  2  the one rule              one sentence, no jargon
  3  a worked parallel example DIFFERENT numbers from the homework
  4  "say this to your child"  one quoted sentence in plain Hindi

§8.2 is explicit that this is "enforced by a fixed answer contract rather than by
prompt vibes", so the contract lives here as code, not as hope in a prompt. A
generated answer that fails validation is never sent: it is retried once, and
then refused. That is what makes a weak fallback model safe — the failure mode
becomes an honest refusal rather than a bad answer (see the tiering note in
DECISIONS.md D4-PRELIM).

Part 3's different-numbers rule is load-bearing. It is what structurally
separates this product from an answer-scanner, and it is the one part of the
contract that can be checked exactly rather than judged — so it is checked
exactly.
"""

from __future__ import annotations

import re
import unicodedata

PARTS = ("answer", "rule", "example", "say_to_child")

# The generator is asked for labelled parts. Labels are matched loosely because a
# model will drift on punctuation and spacing, never on the label itself.
_LABEL_RX = {
    "answer": re.compile(r"^\s*(?:1|१)\s*[.):\-]\s*", re.M),
    "rule": re.compile(r"^\s*(?:2|२)\s*[.):\-]\s*", re.M),
    "example": re.compile(r"^\s*(?:3|३)\s*[.):\-]\s*", re.M),
    "say_to_child": re.compile(r"^\s*(?:4|४)\s*[.):\-]\s*", re.M),
}

MAX_WORDS = 120          # §8.2: under 120 words spoken
MAX_SENTENCES_P1 = 1     # §10: part 1 is one sentence
MAX_SENTENCES_P2 = 1
QUOTE_CHARS = "\"'“”‘’«»"

# Terms a parent with roughly Class 8 schooling and no maths vocabulary would not
# have. §8.2 gives the canonical example: "denominator" must become
# "नीचे वाला अंक". Each of these must be glossed if used at all.
JARGON = {
    "भाज्य": "जिसे बाँटना है",
    "भाजक": "जिससे बाँट रहे हैं",
    "भागफल": "बाँटने पर जो मिला",
    "शेषफल": "जो बच गया",
    "गुणनफल": "गुणा करने पर जो मिला",
    "व्यवकलन": "घटाना",
    "क्रमागत": "एक के बाद एक आने वाली",
    "तुल्य": "एक जैसी",
    "समतुल्य": "एक जैसी",
    "प्रतिरूप": "एक जैसा दोहराव",
    "उर्ध्वाधर": "खड़ी",
    "क्षैतिज": "लेटी हुई",
    "सममिति": "दोनों तरफ एक जैसा",
    "धारिता": "कितना समा सकता है",
    "परिमाप": "चारों तरफ की लंबाई",
    "क्षेत्रफल": "कितनी जगह घेरता है",
}

# Suggested in the prompt but NOT failed on, because each collides with an
# extremely common ordinary word and the check cannot tell them apart:
#   हर   = "denominator", but also "every" ("हर घंटे में 60 मिनट")
#   अंश  = "numerator", but also "portion / passage" — the prompt's own word for
#          the retrieved text, so the model echoed it and was penalised for it
# Flagging these produced 6 false failures out of 30 questions.
JARGON_ADVISORY = {
    "अंश": "ऊपर वाला अंक",
    "हर": "नीचे वाला अंक",
}

_SENT_SPLIT = re.compile(r"[।?!\n]+")

# Numbers written as Hindi words. Without these, the different-numbers rule — the
# one part of the contract that can be checked exactly — has a hole straight
# through it: a model can restate the question's numbers in words
# ("एक तिहाई" for 1/3, "दो छठवाँ" for 2/6) and pass a digit-only check.
HINDI_CARDINALS = {
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5,
    "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11,
    "बारह": 12, "पंद्रह": 15, "बीस": 20, "पच्चीस": 25, "पचास": 50,
    "सौ": 100, "हजार": 1000,
}
# Ordinal-style denominators, as the book and everyday speech form fractions.
HINDI_DENOMS = {
    "आधा": 2, "आधे": 2, "आधी": 2, "तिहाई": 3, "चौथाई": 4, "पाव": 4,
    "पाँचवाँ": 5, "पांचवाँ": 5, "छठवाँ": 6, "छठा": 6, "सातवाँ": 7,
    "आठवाँ": 8, "नौवाँ": 9, "दसवाँ": 10, "बारहवाँ": 12, "बीसवाँ": 20,
}
_CARD_RX = "|".join(sorted(HINDI_CARDINALS, key=len, reverse=True))
_DENOM_RX = "|".join(sorted(HINDI_DENOMS, key=len, reverse=True))
# "दो छठवाँ" -> 2/6 ; a bare "आधा"/"चौथाई" -> 1/2, 1/4
_WORD_FRACTION = re.compile(rf"(?<![ऀ-ॿ])(?:({_CARD_RX})\s+)?({_DENOM_RX})(?![ऀ-ॿ])")
_WORD_CARDINAL = re.compile(rf"(?<![ऀ-ॿ])({_CARD_RX})(?![ऀ-ॿ])")


def hindi_word_numbers(text: str) -> list[str]:
    """Numbers expressed in Hindi words, normalised to the same form as digits."""
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    for m in _WORD_FRACTION.finditer(text or ""):
        num = HINDI_CARDINALS.get(m.group(1) or "", 1)
        den = HINDI_DENOMS[m.group(2)]
        out.append(f"{num}/{den}")
        spans.append(m.span())
    for m in _WORD_CARDINAL.finditer(text or ""):
        # skip a cardinal already consumed as a fraction numerator
        if any(a <= m.start() < b for a, b in spans):
            continue
        out.append(str(HINDI_CARDINALS[m.group(1)]))
    return out


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").strip()


def numbers_in(text: str, words: bool = True) -> list[str]:
    """Numbers as a reader would see them: 1,000 and 1000 are the same number,
    and 1/2 is one number rather than two.

    `words=False` counts only digit-form numbers. The two rules that consume this
    need different things, and conflating them caused a false refusal:

      different-numbers rule  needs word forms, because a model evading it will
                              paraphrase ("एक तिहाई" for 1/3)
      groundedness rule       must NOT count word forms. "10 से गुणा करने पर एक
                              शून्य जुड़ता है" is prose describing a digit, not a
                              numeric claim — but the word expansion read it as
                              the quantities 1 and 0 and called them ungrounded.

    A fabricated figure ("360 डिग्री") is written in digits; descriptive counting
    words are not data.
    """
    out = []
    for m in re.finditer(r"\d+\s*/\s*\d+|\d[\d,]*(?:\.\d+)?", text or ""):
        out.append(re.sub(r"[\s,]", "", m.group()))
    if words:
        out.extend(hindi_word_numbers(text))
    return _reduce_all(out)


def _reduce_all(nums: list[str]) -> list[str]:
    """Compare fractions by value, not by spelling: 2/6 and 1/3 are the same
    number, so an "example" that restates 1/3 as 2/6 must still be caught."""
    out = []
    for n in nums:
        if "/" in n:
            a, b = n.split("/")
            try:
                ai, bi = int(a), int(b)
            except ValueError:
                out.append(n)
                continue
            g = _gcd(ai, bi) or 1
            out.append(f"{ai // g}/{bi // g}")
        else:
            out.append(n)
    return out


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return abs(a)


def split_parts(raw: str) -> dict[str, str]:
    """Cut a generated answer into its four labelled parts."""
    text = _norm(raw)
    marks = []
    for name, rx in _LABEL_RX.items():
        m = rx.search(text)
        if m:
            marks.append((m.start(), m.end(), name))
    marks.sort()
    parts: dict[str, str] = {}
    for i, (_s, end, name) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        parts[name] = text[end:stop].strip()
    return parts


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def validate(raw: str, question: str, context: str | None = None) -> dict:
    """Check a generated answer against the contract.

    Returns {"ok": bool, "failures": [...], "parts": {...}, "stats": {...}}.
    Failures are machine-readable so the retry can be told what to fix.
    """
    parts = split_parts(raw)
    failures: list[dict] = []

    # --- all four parts present, in order (§10, and FR-5: part 4 in 100%) ---
    for name in PARTS:
        if name not in parts or not parts[name]:
            failures.append({"code": "missing_part", "part": name})

    # --- length (§8.2: under 120 words spoken) ---
    words = len(_norm(raw).split())
    if words > MAX_WORDS:
        failures.append({"code": "too_long", "words": words, "limit": MAX_WORDS})

    # --- parts 1 and 2 are one sentence each (§10) ---
    for name, limit in (("answer", MAX_SENTENCES_P1), ("rule", MAX_SENTENCES_P2)):
        if name in parts:
            n = len(_sentences(parts[name]))
            if n > limit:
                failures.append({"code": "not_one_sentence", "part": name, "sentences": n})

    # --- part 3 must use DIFFERENT numbers from the question (§10, §12.1) ---
    #
    # These checks only apply to a NUMERIC concept. §10 calls part 3 "a worked
    # parallel example", and the different-numbers rule exists to stop the parent
    # transcribing a computed answer. Where there is no computation there is
    # nothing to transcribe: "दर्पण जैसी आकृति कैसे बनाते हैं?" and
    # "सम पंचभुज से टाइल क्यों नहीं बन पाती?" have perfectly good worked examples
    # with no numbers in them. Demanding numbers there produced 4 false failures
    # out of 30 and would push the model to invent figures — the opposite of what
    # the groundedness rule wants.
    q_nums = set(numbers_in(question))
    numeric_concept = bool(q_nums) or bool(numbers_in(context or "", words=False))
    if "example" in parts and numeric_concept:
        ex_nums = set(numbers_in(parts["example"]))
        if not ex_nums:
            failures.append({"code": "example_has_no_numbers"})
        # §10: the worked example must use DIFFERENT numbers from the homework,
        # so the parent cannot transcribe its result into the exercise.
        #
        # Strict zero overlap is UNSATISFIABLE for conceptual questions. Asked
        # "10 और 100 से गुणा करने पर क्या होता है?", no honest example can avoid
        # mentioning 10 — the number IS the concept. Requiring mere novelty is too
        # weak in the other direction: "जैसे 1/3 को 2 से गुणा करें तो 2/6 मिलता है"
        # introduces 2 and still re-derives the exact question.
        #
        # What actually distinguishes them is how much genuinely new arithmetic the
        # example carries. A transcription reproduces every number the question
        # gave and adds almost nothing; a real parallel example brings fresh
        # operands even when it must reuse the operator.
        new_nums = ex_nums - q_nums
        if not new_nums:
            failures.append(
                {"code": "example_introduces_no_new_numbers",
                 "question_numbers": sorted(q_nums)}
            )
        elif q_nums and q_nums <= ex_nums and len(new_nums) <= 1:
            failures.append(
                {"code": "example_reuses_question_numbers",
                 "shared": sorted(q_nums), "new": sorted(new_nums)}
            )

    # --- part 4 must be a quoted sentence the parent can say aloud (§10) ---
    if "say_to_child" in parts:
        p4 = parts["say_to_child"]
        if not any(ch in p4 for ch in QUOTE_CHARS):
            failures.append({"code": "part4_not_quoted"})
        if len(_sentences(p4)) > 2:
            failures.append({"code": "part4_too_long", "sentences": len(_sentences(p4))})

    # --- no unglossed jargon (§8.2) ---
    body = _norm(raw)
    q_norm = _norm(question)
    for term, plain in JARGON.items():
        # A term the parent used themselves is vocabulary they already have; the
        # rule exists to stop US introducing words they do not know (§8.2).
        # "धारिता या क्षमता कैसे मापते हैं?" must not be failed for saying धारिता.
        if re.search(rf"(?<![ऀ-ॿ]){term}(?![ऀ-ॿ])", q_norm):
            continue
        if re.search(rf"(?<![ऀ-ॿ]){term}(?![ऀ-ॿ])", body):
            # a term is acceptable when its plain-language gloss sits beside it
            gloss_near = re.search(
                rf"(?<![ऀ-ॿ]){term}[^।?!\n]{{0,40}}{re.escape(plain.split()[0])}", body
            ) or re.search(
                rf"{re.escape(plain.split()[0])}[^।?!\n]{{0,40}}(?<![ऀ-ॿ]){term}", body
            )
            if not gloss_near:
                failures.append({"code": "unglossed_jargon", "term": term, "plain": plain})

    # --- Devanagari, not transliteration or English ---
    dev = len(re.findall(r"[ऀ-ॿ]", body))
    latin = len(re.findall(r"[A-Za-z]", body))
    letters = dev + latin
    # Proportional, not an absolute character count: a short honest decline
    # ("पाठ में समकोण की बात नहीं है।") is 23 Devanagari characters and was being
    # failed as "not Hindi" for being brief.
    if letters and dev / letters < 0.7:
        failures.append({"code": "not_hindi", "devanagari_chars": dev, "latin": latin})
    elif latin > dev * 0.15:
        failures.append({"code": "too_much_latin", "latin": latin, "devanagari": dev})

    # --- groundedness (FR-3, §7.3: ungrounded-claim rate at or below 1%) ---
    #
    # Contract conformance is NOT groundedness. A measured example: asked why
    # regular pentagons cannot tile, the model returned a perfectly conformant
    # answer built on "angles at a point sum to 360°, a pentagon's corner is
    # 108°". Correct maths — and absent from the Class 5 page, which explains it
    # visually as a leftover gap. Angle sums in degrees are Class 6-7. So the
    # parent would have taught their child a rule the child has not been given.
    #
    # Parts 1 and 2 must stay inside the retrieved text, so any number they cite
    # has to appear there. Part 3 is exempt by construction: its whole job is to
    # use DIFFERENT numbers.
    if context:
        ctx_nums = set(numbers_in(context, words=False)) | set(
            numbers_in(question, words=False)
        )
        for part in ("answer", "rule"):
            if part not in parts:
                continue
            for n in numbers_in(parts[part], words=False):
                if n not in ctx_nums:
                    failures.append(
                        {"code": "ungrounded_number", "part": part, "number": n}
                    )

    return {
        "ok": not failures,
        "failures": failures,
        "parts": parts,
        "stats": {
            "words": words,
            "devanagari_chars": dev,
            "question_numbers": sorted(q_nums),
            "example_numbers": sorted(numbers_in(parts.get("example", ""))),
        },
    }


def failures_as_instruction(failures: list[dict]) -> str:
    """Turn validation failures into a correction the model can act on, for the
    single retry before the gate gives up and refuses."""
    lines = []
    for f in failures:
        code = f["code"]
        if code == "missing_part":
            lines.append(f"- भाग {PARTS.index(f['part']) + 1} पूरी तरह गायब है, उसे लिखिए।")
        elif code == "too_long":
            lines.append(f"- उत्तर {f['words']} शब्दों का है; {f['limit']} शब्दों से कम कीजिए।")
        elif code == "not_one_sentence":
            lines.append(
                f"- भाग {PARTS.index(f['part']) + 1} में {f['sentences']} वाक्य हैं; "
                "सिर्फ एक वाक्य रखिए।"
            )
        elif code == "example_reuses_question_numbers":
            lines.append(
                f"- भाग 3 सवाल को ही दोहरा रहा है ({', '.join(f['shared'])}); "
                "उसी तरीके को बिलकुल नई संख्याओं पर लगाकर दिखाइए।"
            )
        elif code == "example_introduces_no_new_numbers":
            lines.append(
                "- भाग 3 में कोई नई संख्या नहीं है; वही तरीका नई संख्याओं पर "
                "हल कर के दिखाइए।"
            )
        elif code == "example_has_no_numbers":
            lines.append("- भाग 3 में कोई संख्या नहीं है; अलग संख्याओं से हल कर के दिखाइए।")
        elif code == "part4_not_quoted":
            lines.append('- भाग 4 को उद्धरण चिह्नों में लिखिए, जैसे "…"।')
        elif code == "part4_too_long":
            lines.append("- भाग 4 सिर्फ एक वाक्य का होना चाहिए।")
        elif code == "unglossed_jargon":
            lines.append(f"- '{f['term']}' शब्द न लिखिए; उसकी जगह '{f['plain']}' लिखिए।")
        elif code == "ungrounded_number":
            lines.append(
                f"- '{f['number']}' किताब के अंश में नहीं है; भाग "
                f"{PARTS.index(f['part']) + 1} में केवल वही बात लिखिए जो अंश में दी गई है।"
            )
        elif code in ("not_hindi", "too_much_latin"):
            lines.append("- पूरा उत्तर देवनागरी हिंदी में लिखिए।")
    return "\n".join(dict.fromkeys(lines))
