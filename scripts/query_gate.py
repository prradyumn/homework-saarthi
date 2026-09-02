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
_TOPIC_RX = re.compile(r"(?<![ऀ-ॿ])(?:" + "|".join(TOPIC_WORDS) + r")")

# Requests that ask the product to be an answer-vending machine. Refusing these
# is a positioning decision (§4), not a confidence decision: answering them well
# would make the product worse.
_COPYING_RX = re.compile(
    r"सारे\s*सवाल|सब\s*सवाल|सभी\s*सवाल|जवाब\s*लिखकर|सिर्फ\s*जवाब|"
    r"केवल\s*जवाब|बिना\s*समझाए|होमवर्क\s*कर(ा|\s*दी)|पूरा\s*होमवर्क"
)

# Any Latin letter alongside Devanagari is an ASR-garble signature. The first
# version required a run of 2+ Latin characters and so missed "कोन कैसे नापt hai".
# A Hindi maths question from this book has no reason to contain Latin at all —
# the textbook writes units in Devanagari (सें.मी., कि.ग्रा.).
_LATIN_IN_DEV = re.compile(r"(?=.*[ऀ-ॿ])(?=.*[A-Za-z])", re.DOTALL)

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
    return re.findall(r"[ऀ-ॿ]+", q)


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
    g: re.compile(r"(?<![ऀ-ॿ])(?:" + "|".join(ws) + r")")
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

    # two or more numbers is itself a maths signal, for questions phrased entirely
    # in everyday words ("100 नारियल को 8 में बाँटना है")
    numeric = len(re.findall(r"\d+", q)) >= 2
    has_topic = bool(_TOPIC_RX.search(q)) or numeric
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


def has_beyond_class5_word(question: str) -> bool:
    """True if the question names a topic that is not in the Class 5 book at all."""
    return bool(_BEYOND_Q_RX.search(question))
