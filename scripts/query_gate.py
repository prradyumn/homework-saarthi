"""Query-side pre-checks, applied BEFORE retrieval.

Why this exists: the calibration showed a single similarity threshold being
forced up to 0.645 — destroying coverage — in order to catch leaks it was never
the right tool for. Broken down by refusal class, the decoy/metadata signal is
already perfect on genuinely out-of-syllabus maths (23/23: algebra 0/7,
higher-number 0/8, geometry 0/2, statistics 0/2, trigonometry 0/1, off-topic 0/3
wrongly matched Class 5). Every remaining leak belongs to a class that retrieval
confidence cannot see:

  missing_context   "इसका जवाब क्या है?"          — nothing to retrieve against
  two_questions     "भिन्न क्या है और कोण कैसे?"  — two answers wanted, contract gives one
  answer_copying    "सारे सवालों के जवाब भेज दीजिए" — contradicts §4 positioning
  figure_only       answer lives in a diagram      — chunk flag, not a score
  badly_phrased     ASR garble                     — FR-2's confirmation turn

So each is handled by the mechanism suited to it, and the similarity threshold is
left to handle only the residual. These checks are deterministic string work: no
model, no cost, no latency, and auditable.

A pre-check that fires does NOT always mean a bare refusal. `clarify` outcomes
should re-prompt the parent, which is cheaper for them than a refusal and is
already in the product's grain (FR-2 asks a clarifying question anyway).
"""

from __future__ import annotations

import re

# Class 5 maths topic vocabulary. A question with no topic word and no numbers is
# not answerable from a maths textbook, whatever it scores against it.
TOPIC_WORDS = [
    "भिन्न", "अंश", "हर", "आधा", "आधे", "चौथाई", "तिहाई", "पूर्ण",
    "संख्या", "अंक", "इकाई", "दहाई", "सैकड़ा", "हजार", "गिन", "अल्पविराम",
    "जोड़", "घटा", "गुणा", "गुणन", "भाग", "गुणज", "योग", "व्यवकलन", "शेष",
    "भाज्य", "भाजक", "भागफल", "सम", "विषम", "हासिल",
    "कोण", "समकोण", "घुमाव", "त्रिभुज", "वर्ग", "पंचभुज", "षट्भुज", "भुजा",
    "आकृति", "आकार", "टाइल", "प्रतिरूप", "सममिति", "घन", "टैनग्राम", "फिरकी",
    "क्षेत्रफल", "परिमाप", "लंबाई", "ऊँचाई", "दूरी", "मीटर", "सेंटीमीटर",
    "किलोमीटर", "मापन", "माप", "नाप", "इकाइयाँ",
    "भार", "तोल", "तराजू", "किलोग्राम", "ग्राम", "किलो", "धारिता", "क्षमता",
    "लीटर", "मिलीलीटर",
    "समय", "घंटा", "घंटे", "घंटो", "मिनट", "सेकंड", "दिनचर्या", "घड़ी",
    "मानचित्र", "नक्शा", "नक्शे", "दिशा", "दिशाएँ", "उत्तर", "दक्षिण",
    "पूरब", "पश्चिम", "अवस्थिति",
    "आँकड़", "आंकड़", "तालिका", "दंड", "आरेख", "चित्रालेख", "गणित",
    "रुपये", "रुपए", "कीमत", "बचत", "संख्याओं", "अनुमान", "अंदाजा", "आकलन",
    # added after the pre-check produced false positives on real questions:
    # design/quilt/tiling vocabulary is genuine Class 5 content (ch10, ch11)
    "बाँट", "बांट", "चौकोर", "डिजाइन", "अभिकल्पना", "टुकड़", "घुमा",
    "ब्लॉक", "छपाई", "रजाई", "कटआउट", "मोड़",
]
# Prefix guard so a keyword only matches at a word start, AND a suffix guard so it
# cannot be the opening syllables of a different word. Hindi inflects with matras
# (भिन्न -> भिन्नों), so suffixes must be allowed — but a following CONSONANT means
# a different word entirely, and without that guard "भारत" (India) matched the
# topic word "भार" (weight), letting a general-knowledge question through the gate
# to be refused later on a similarity score and offered an irrelevant page.
# Explicit codepoints: a literal [क-हक़-य़] is an invalid range, because क़ is
# two codepoints (क + nukta) rather than one.
_CONSONANT = "[\u0915-\u0939\u0958-\u095f]"
# A following consonant usually means a different word (भार -> भारत), but Hindi
# verb inflection adds consonant-initial endings to the same stem
# (जोड़ -> जोड़ने, माप -> मापना). Matra-initial endings (भिन्नों, भिन्नें) need no
# exception because a matra is not a consonant. So: block a following consonant
# UNLESS it opens a known inflection.
_INFLECTIONS = ("ना", "ने", "नी", "कर", "ते", "ता", "ती", "या", "वा")
_STEM_END = f"(?:(?!{_CONSONANT})|(?={'|'.join(_INFLECTIONS)}))"
_TOPIC_RX = re.compile(
    r"(?<![ऀ-ॿ])(?:" + "|".join(TOPIC_WORDS) + r")" + _STEM_END
)

# The same Class 5 topics in English, because a parent may type or say the
# question in English even when the child's textbook is Hindi. Kept as a separate
# list with its own word-boundary rule: splicing Latin into _TOPIC_RX would apply
# the Devanagari stem-end guard to it, which is meaningless for English.
TOPIC_WORDS_EN = [
    "fraction", "fractions", "numerator", "denominator", "half", "quarter",
    "third", "whole", "number", "numbers", "digit", "digits", "place value",
    "ones", "tens", "hundreds", "thousand", "count", "counting", "comma",
    "add", "adding", "addition", "plus", "subtract", "subtraction", "minus",
    "multiply", "multiplication", "times table", "divide", "division",
    "dividend", "divisor", "quotient", "remainder", "multiple", "multiples",
    "factor", "factors", "sum", "carry", "carrying", "borrow", "borrowing",
    "even", "odd", "estimate", "estimation", "rounding", "round off",
    "angle", "angles", "right angle", "turn", "rotation", "triangle",
    "square", "rectangle", "pentagon", "hexagon", "side", "sides", "shape",
    "shapes", "tile", "tiles", "tiling", "pattern", "patterns", "symmetry",
    "symmetric", "cube", "tangram",
    "area", "perimeter", "length", "height", "distance", "metre", "meter",
    "centimetre", "centimeter", "kilometre", "kilometer", "measure",
    "measuring", "measurement", "unit", "units",
    "weight", "weigh", "weighing", "balance", "kilogram", "gram", "kilo",
    "capacity", "volume", "litre", "liter", "millilitre", "milliliter",
    "time", "hour", "hours", "minute", "minutes", "second", "seconds",
    "clock", "calendar", "routine",
    "map", "direction", "directions", "north", "south", "east", "west",
    "position", "grid",
    "money", "rupee", "rupees", "paise", "cost", "price", "bill", "change",
    "graph", "bar graph", "chart", "table", "data", "tally",
    "maths", "math", "mathematics", "sum", "problem", "homework",
    # Unit abbreviations, which is how people actually write them even mid-Hindi
    # ("10 m में कितने cm होते हैं?"). Without these such a question read as
    # "not a maths question". The single letters are safe here because the
    # algebra-notation check keys on [xyzn] fused to a digit, not on these.
    "cm", "mm", "km", "kg", "gm", "ml", "hr", "min", "sec",
]
_TOPIC_EN_RX = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in
                        sorted(TOPIC_WORDS_EN, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Requests that ask the product to be an answer-vending machine. Refusing these
# is a positioning decision (§4), not a confidence decision: answering them well
# would make the product worse.
_COPYING_RX = re.compile(
    r"सारे\s*सवाल|सब\s*सवाल|सभी\s*सवाल|जवाब\s*लिखकर|सिर्फ\s*जवाब|"
    r"केवल\s*जवाब|बिना\s*समझाए|होमवर्क\s*कर(ा|\s*दी)|पूरा\s*होमवर्क"
)

# This used to refuse ANY Latin letter alongside Devanagari as an ASR-garble
# signature. That is no longer tenable, for two reasons:
#
#   1. English input is now supported, and code-mixing is how people actually
#      write — "1 kg में कितने ग्राम?" is a perfectly clear question.
#   2. It mislabelled typed algebra: "x2+5x+6 का हल क्या है?" was told "say it
#      again", when nothing had been said and the real issue was that the topic
#      is beyond Class 5 (recorded in D12 as an open bug; this closes it).
#
# The genuine garble signature is a SHORT Latin fragment FUSED to Devanagari with
# no space — "नापt", "hai" glued to a word — not Latin words standing alone. And
# the designed defence against a mis-heard question is FR-2's confirmation turn,
# which shows the parent what was heard; this rule was always a second guess at
# the same problem.
_LATIN_IN_DEV = re.compile(
    r"[ऀ-ॿ][A-Za-z]{1,2}(?![A-Za-z])|(?<![A-Za-z])[A-Za-z]{1,2}[ऀ-ॿ]"
)

# Questions that POINT AT a figure. This is the right place to catch the
# figure-only class: the signal is in the question ("चित्र में दिखाई गई…",
# "नक्शे में…", "तालिका में…"), not in the chunk. The chunk-side
# `figure_dependent` flag was mis-calibrated in BOTH directions — it discarded 12
# of 100 answerable questions while still missing 3 of 4 figure-only ones — so it
# stays as metadata but no longer gates.
_FIGURE_REF = re.compile(
    r"(?<![ऀ-ॿ])(?:चित्र\s*में|आकृति\s*में|नक्शे\s*में|नक्शा\s*में|मानचित्र\s*में|"
    r"तालिका\s*में|आरेख\s*में|ग्राफ\s*में|दिखाई\s*गई|दिए\s*गए\s*चित्र)"
)

_VALUE_SEEKING = re.compile(r"(?<![ऀ-ॿ])(?:कहाँ|कितने|कितना|कितनी|क्या|कौन)")
_METHOD_SEEKING = re.compile(r"(?<![ऀ-ॿ])(?:कैसे|मतलब|क्यों|तरीका|विधि|समझा)")

MIN_TOKENS = 4


def _tokens(q: str) -> list[str]:
    """Words in either script.

    Devanagari-only tokenising made the gate blind to English: every English
    question yielded zero tokens, fell under MIN_TOKENS, and was refused as
    `too_short` — including good ones like "how many grams in one kilogram?".
    """
    return re.findall(r"[ऀ-ॿ]+|[A-Za-z][A-Za-z']*", q)


# Topic groups. Two asks only count as two questions if they reach into DIFFERENT
# groups — otherwise "गुणा करने के और कौन कौन से तरीके हैं?" reads as two questions
# because "और" means "more" and "कौन कौन" is ordinary Hindi reduplication, and
# "1/3 और 2/6 एक जैसी कैसे हैं?" reads as two because it has two question marks.
TOPIC_GROUPS = {
    "fractions": ["भिन्न", "अंश", "हर", "आधा", "आधे", "चौथाई", "तिहाई"],
    "numbers": ["संख्या", "अंक", "इकाई", "दहाई", "सैकड़ा", "हजार", "अल्पविराम", "संख्याओं"],
    "operations": ["जोड़", "घटा", "गुणा", "गुणन", "भाग", "गुणज", "योग", "व्यवकलन",
                   "शेष", "भाज्य", "भाजक", "भागफल", "हासिल", "बाँट", "बांट"],
    "geometry": ["कोण", "समकोण", "घुमाव", "त्रिभुज", "वर्ग", "पंचभुज", "षट्भुज",
                 "भुजा", "आकृति", "आकार", "टाइल", "प्रतिरूप", "सममिति", "घन",
                 "टैनग्राम", "चौकोर", "डिजाइन"],
    "length": ["लंबाई", "ऊँचाई", "दूरी", "मीटर", "सेंटीमीटर", "किलोमीटर", "क्षेत्रफल"],
    "weight": ["भार", "तोल", "तराजू", "किलोग्राम", "ग्राम", "किलो", "धारिता",
               "क्षमता", "लीटर", "मिलीलीटर"],
    "time": ["समय", "घंटा", "घंटे", "घंटो", "मिनट", "सेकंड", "दिनचर्या", "घड़ी"],
    "maps": ["मानचित्र", "नक्शा", "नक्शे", "दिशा", "दिशाएँ", "अवस्थिति"],
    "data": ["आँकड़", "आंकड़", "तालिका", "दंड", "आरेख", "चित्रालेख"],
}
_GROUP_RX = {
    g: re.compile(r"(?<![ऀ-ॿ])(?:" + "|".join(ws) + r")" + _STEM_END)
    for g, ws in TOPIC_GROUPS.items()
}


def topic_groups(q: str) -> set[str]:
    return {g for g, rx in _GROUP_RX.items() if rx.search(q)}


def _question_clauses(q: str) -> int:
    """Two asks only count as two questions when they span different topic groups,
    so the four-part contract genuinely cannot serve both in one reply."""
    interrogatives = re.findall(
        r"(?<![ऀ-ॿ])(?:क्या|कैसे|कितने|कितना|कौन|कहाँ|क्यों|कब)", q
    )
    marks = q.count("?") + q.count("？")
    if (marks >= 2 or len(interrogatives) >= 2) and len(topic_groups(q)) >= 2:
        return 2
    return 1


def pre_check(question: str) -> dict:
    """Returns {"outcome": "pass"|"clarify"|"refuse", "reason": str|None}."""
    q = question.strip()
    toks = _tokens(q)

    if _COPYING_RX.search(q):
        return {"outcome": "refuse", "reason": "answer_copying"}

    if _question_clauses(q) >= 2:
        return {"outcome": "clarify", "reason": "two_questions"}

    if _LATIN_IN_DEV.search(q):
        return {"outcome": "clarify", "reason": "asr_suspect_code_mixed"}

    # Referring to a figure is not itself disqualifying — what matters is whether
    # the question asks for a METHOD or for a VALUE read off that figure.
    #   "नक्शे में दिशाएँ कैसे देखते हैं?"      -> method, answerable from text
    #   "नक्शे में चिड़ियाघर का शेर कहाँ है?"   -> lookup, answerable only from the figure
    # So a figure reference refuses only alongside a value-seeking interrogative,
    # and never alongside कैसे / मतलब / क्यों, which ask for the method or the idea.
    if _FIGURE_REF.search(q) and _VALUE_SEEKING.search(q) and not _METHOD_SEEKING.search(q):
        return {"outcome": "refuse", "reason": "figure_value_lookup"}

    # A recognised higher-class maths term settles the question on its own, even
    # when a Class 5 topic word also appears — "square root" contains "square",
    # and प्रतिशत questions usually carry numbers. Gating this behind "no Class 5
    # topic found" let "what is the square root of 49?" through.
    #
    # It also has to run BEFORE the topic test, not after: वर्गमूल and प्रतिशत were
    # being answered with "this does not look like a maths question", which is
    # false and corrodes the one thing §8.1 is trying to earn. Third instance of
    # an early code pre-empting a more accurate later layer.
    if has_beyond_class5_word(q):
        return {"outcome": "refuse", "reason": "beyond_class5_topic_in_question"}

    # two or more numbers is itself a maths signal, for questions phrased entirely
    # in everyday words ("100 नारियल को 8 में बाँटना है")
    numeric = len(re.findall(r"\d+", q)) >= 2
    has_topic = bool(_TOPIC_RX.search(q)) or bool(_TOPIC_EN_RX.search(q)) or numeric
    if len(toks) < MIN_TOKENS and not has_topic:
        return {"outcome": "clarify", "reason": "too_short"}
    if not has_topic:
        return {"outcome": "clarify", "reason": "no_maths_topic"}

    return {"outcome": "pass", "reason": None}


def is_value_seeking(question: str) -> bool:
    """Does the question ask for a specific value rather than for a method?

    Used with a chunk-side check: if a question asks "कितने…" and the best chunk
    contains no numbers at all, the number is not in the text — it is in a figure.
    That is how the p183 bar-chart question ("शीला ने रमन की अपेक्षा अध्ययन पर
    कितने घंटे अधिक समय व्यतीत किया?") slips past every other signal: it names no
    figure, and the chunk it retrieves is the right one, but the chunk's prose
    never states the bar heights.
    """
    return bool(_VALUE_SEEKING.search(question)) and not _METHOD_SEEKING.search(question)


# Topics verified absent from the Class 5 corpus (each checked for zero
# occurrences in ingest/chunks.json). Kept here, on the QUERY side, because
# D1's lesson keeps repeating: a categorical signal beats a tuned score.
#
# The decoy corpus can only reach 73% coverage, and the ceiling is structural —
# Class 6-8 books teach Class 5 topics at greater length, so they win on
# similarity for legitimate questions ("सम और विषम संख्या में क्या फर्क है?"
# lost to a Class 7 chunk by 0.071). Reading the question's own vocabulary needs
# no corpus and no threshold.
BEYOND_CLASS5_WORDS = [
    "बीजीय", "समीकरण", "व्यंजक", "सर्वसमिका", "बहुपद", r"चर\s*राशि",
    "घातांक", "वर्गमूल", "घनमूल", "प्रतिशत", "अनुपात", "समानुपात", "ब्याज",
    "परिमेय", "अपरिमेय", "दशमलव", "निर्देशांक", "सर्वांगसम", "प्रमेय",
    "प्रायिकता", "माध्यिका", "बहुलक", "त्रिकोणमिति", "ऋणात्मक", "पूर्णांक",
    "द्विघात", "गुणनखंडन", "पाइथागोरस", "साइन", "कोज्या",
]
_BEYOND_Q_RX = re.compile(r"(?<![ऀ-ॿ])(?:" + "|".join(BEYOND_CLASS5_WORDS) + r")")

# The English equivalents, so "how do I solve a quadratic equation?" is refused
# for the true reason rather than falling through to "not a maths question".
BEYOND_CLASS5_WORDS_EN = [
    "algebra", "algebraic", "equation", "equations", "expression", "identity",
    "polynomial", "variable", "exponent", "exponents",
    "square root", "cube root", "percent", "percentage", "ratio",
    "proportion", "interest", "rational", "irrational", "decimal", "decimals",
    "coordinate", "coordinates", "congruent", "theorem", "probability",
    "median", "trigonometry", "sine", "cosine",
    "negative number", "integer", "integers", "quadratic", "factorise",
    "factorize", "pythagoras", "pythagorean", "calculus", "logarithm",
]
_BEYOND_EN_RX = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in
                        sorted(BEYOND_CLASS5_WORDS_EN, key=len, reverse=True))
    + r")\b", re.IGNORECASE)


# Algebraic NOTATION, which carries no keyword at all: "x2+5x+6 का हल क्या है?"
# names no topic, so word lists cannot see it. Class 5 maths has no variables, so
# a lone letter fused to a digit is a reliable signal — restricted to the letters
# actually used as variables and deliberately excluding unit letters, because
# "10 m", "2 cm" and "5 kg" must not match. Requires no space, so "5 x 3" (a
# multiplication a parent might type) is untouched.
_ALGEBRA_NOTATION = re.compile(r"(?<![A-Za-z])[xyzn]\d|\d[xyzn](?![A-Za-z])")


def has_beyond_class5_word(question: str) -> bool:
    """True if the question names a topic that is not in the Class 5 book at all."""
    return bool(_BEYOND_Q_RX.search(question)
                or _BEYOND_EN_RX.search(question)
                or _ALGEBRA_NOTATION.search(question))
