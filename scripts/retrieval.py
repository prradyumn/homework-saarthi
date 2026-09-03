"""Hybrid retrieval: dense embeddings plus a lexical (IDF) signal.

Why hybrid. A 30-question generation eval had 8 questions where the model
correctly declined, because the passages it received did not contain the answer.
Chapter-level retrieval accuracy of 97% had hidden this: the right chapter was
found, the wrong chunk within it was returned.

Inspecting them split the cause in two:

  - "चित्रालेख क्या होता है?" — five chunks contain the literal word चित्रालेख,
    and dense retrieval surfaced none of them. Cosine similarity over a whole
    passage dilutes a single decisive term; a parent asking what a word means is
    exactly the case where the word itself is the strongest signal.
  - "दर्पण जैसी आकृति कैसे बनाते हैं?" — NO chunk anywhere in the corpus contains
    दर्पण or सममित. Chapter 10 teaches symmetry entirely through figures, so the
    question is not answerable from text at any retrieval quality and the decline
    is correct (D0.1). No retrieval change can or should fix that one.

So the lexical term is aimed only at the first class. IDF is computed over the
Class 5 corpus, with no new dependency: a term that appears in few chunks carries
more weight, so a rare content word like चित्रालेख outweighs common scaffolding
like कैसे or क्या.
"""

from __future__ import annotations

import math
import re
from collections import Counter

# Devanagari word tokens, plus digits and inline fractions so "1/2" is a term.
_TOKEN_RX = re.compile(r"[ऀ-ॿ]+|\d+/\d+|\d+")

# Scaffolding that carries no topic information. Left in the IDF corpus (so its
# weight stays low naturally) but dropped from the query so it cannot dominate a
# short question.
STOPWORDS = {
    "क्या", "कैसे", "कितने", "कितना", "कितनी", "कौन", "कहाँ", "क्यों", "कब",
    "है", "हैं", "हो", "होता", "होती", "होते", "था", "थे", "की", "के", "का",
    "को", "में", "से", "पर", "और", "यह", "वह", "ये", "वे", "एक", "इस", "उस",
    "मेरे", "मेरी", "अपने", "अपनी", "बच्चे", "बच्चा", "बेटी", "बेटा", "रही",
    "रहा", "दीजिए", "कीजिए", "बताइए", "समझा", "सिखाऊँ", "करें", "करते", "क्रम",
    "मतलब", "फर्क", "अंतर", "तरीका", "वाला", "वाली", "जैसी", "जैसे", "नहीं",
    "आ", "ना", "तो", "भी", "या", "ही",
}


def tokens(text: str) -> list[str]:
    return _TOKEN_RX.findall(text or "")


# ---------------------------------------------------------------------------
# Parent vocabulary -> textbook vocabulary.
#
# §3.1 says the parent speaks fluent Hindi but did not finish the schooling the
# book is written for. That shows up as a measurable retrieval failure: the word
# the parent uses is simply not in the book.
#
# Audited against the corpus (scripts/audit_answerability.py). Every LEFT-hand
# word below occurs ZERO times in 188k characters of the textbook; every
# right-hand word was checked to occur, with its count:
#
#   parent says       book says                          content present?
#   नक्शा (0)          मानचित्र (25), दिशा (47), मार्ग (32)   yes, fully
#   सममिति (0)         अक्ष (24), मोड़ (37), अभिकल्पना (25)   yes — ch10 teaches
#                                                          symmetry by folding
#                                                          about an axis, and
#                                                          never names it
#   डिजाइन in ch11 (0) वर्ग (54), चौकोर, टुकड़               yes
#
# This is why three chapters looked "unanswerable" and were nearly re-labelled
# as figure-only. They are answerable; the question and the book were using
# different words for the same thing. Mapping is the fix, not re-labelling.
PARENT_TO_BOOK: dict[str, list[str]] = {
    "नक्शा": ["मानचित्र", "दिशा"],
    "नक्शे": ["मानचित्र", "दिशा"],
    "सममिति": ["अक्ष", "मोड़", "अभिकल्पना"],
    "सममित": ["अक्ष", "मोड़"],
    "दर्पण": ["अक्ष", "मोड़"],
    "रास्ता": ["मार्ग"],
    "पहाड़ा": ["गुणन", "गुणा"],
    "बटा": ["भिन्न"],
    "पौना": ["भिन्न"],
    "डेढ़": ["भिन्न"],
    "वजन": ["भार"],
    "नापना": ["माप", "मापन"],
    "पैसा": ["रुपये"],
    "रुपया": ["रुपये"],
    "बाकी": ["शेष", "शेषफल"],
    "तिकोना": ["त्रिभुज"],
    "भारी": ["भार"],
    "हल्का": ["भार"],
    "खाली": ["रिक्त"],
    "दुगना": ["दोगुना"],
    "तिगुना": ["गुना", "गुणन"],
    "चौगुना": ["गुना", "गुणन"],
}


# English -> the textbook's Hindi, for the same reason PARENT_TO_BOOK exists but
# across scripts rather than registers. BGE-M3 is multilingual, so dense
# retrieval already finds the right chapter from English (5/5 on a hand check),
# but the hybrid's IDF term-overlap half contributes nothing when the query
# shares no tokens with a Hindi corpus. That showed up as scores of 0.42-0.59
# for English against 0.87-0.98 for the same question in Hindi — enough headroom
# lost to matter against the 0.08 decoy margin.
#
# Only terms that appear in this book, verified against the corpus vocabulary.
ENGLISH_TO_BOOK: dict[str, list[str]] = {
    "fraction": ["भिन्न"], "fractions": ["भिन्न"],
    "numerator": ["अंश"], "denominator": ["हर"],
    "half": ["आधा"], "quarter": ["चौथाई"], "third": ["तिहाई"],
    "number": ["संख्या"], "numbers": ["संख्या"], "digit": ["अंक"],
    "place": ["स्थानीय", "मान"], "value": ["मान"],
    "tens": ["दहाई"], "hundreds": ["सैकड़ा"], "thousand": ["हजार"],
    "add": ["जोड़", "योग"], "addition": ["जोड़", "योग"], "adding": ["जोड़"],
    "carry": ["हासिल"], "carrying": ["हासिल"],
    "subtract": ["घटा", "व्यवकलन"], "subtraction": ["घटा", "व्यवकलन"],
    "multiply": ["गुणा"], "multiplication": ["गुणा", "गुणन"],
    "divide": ["भाग"], "division": ["भाग"], "quotient": ["भागफल"],
    "divisor": ["भाजक"], "dividend": ["भाज्य"], "remainder": ["शेषफल"],
    "multiple": ["गुणज"], "multiples": ["गुणज"], "factor": ["गुणनखंड"],
    "even": ["सम"], "odd": ["विषम"], "estimate": ["आकलन"],
    "angle": ["कोण"], "angles": ["कोण"], "turn": ["घुमाव"],
    "triangle": ["त्रिभुज"], "square": ["वर्ग"], "rectangle": ["आयत"],
    "pentagon": ["पंचभुज"], "hexagon": ["षट्भुज"], "side": ["भुजा"],
    "shape": ["आकृति"], "shapes": ["आकृति"], "tile": ["टाइल"],
    "tiling": ["टाइल"], "pattern": ["प्रतिरूप"], "symmetry": ["सममिति"],
    "area": ["क्षेत्रफल"], "perimeter": ["परिमाप"], "length": ["लंबाई"],
    "height": ["ऊँचाई"], "distance": ["दूरी"],
    "metre": ["मीटर"], "meter": ["मीटर"], "centimetre": ["सेंटीमीटर"],
    "centimeter": ["सेंटीमीटर"], "kilometre": ["किलोमीटर"],
    "cm": ["सेंटीमीटर"], "km": ["किलोमीटर"],
    "measure": ["माप"], "measurement": ["मापन", "माप"], "unit": ["इकाई"],
    "weight": ["भार"], "weigh": ["तोल"], "balance": ["तराजू"],
    "kilogram": ["किलोग्राम"], "kg": ["किलोग्राम"], "gram": ["ग्राम"],
    "kilo": ["किलो"], "capacity": ["धारिता", "क्षमता"],
    "litre": ["लीटर"], "liter": ["लीटर"], "millilitre": ["मिलीलीटर"],
    "time": ["समय"], "hour": ["घंटा"], "minute": ["मिनट"],
    "second": ["सेकंड"], "clock": ["घड़ी"],
    "map": ["मानचित्र"], "direction": ["दिशा"], "north": ["उत्तर"],
    "south": ["दक्षिण"], "east": ["पूरब"], "west": ["पश्चिम"],
    "money": ["रुपये"], "rupee": ["रुपये"], "rupees": ["रुपये"],
    "cost": ["मूल्य"], "price": ["मूल्य"],
    "graph": ["आरेख"], "chart": ["आरेख"], "table": ["तालिका"],
    "teach": ["सिखा"], "explain": ["समझा"], "child": ["बच्चे"],
}
_EN_TOKEN_RX = __import__("re").compile(r"[A-Za-z]+")


def expand_query(question: str) -> str:
    """Append the textbook's words for any parent-vocabulary term used.

    The original question is kept intact — the expansion is additive, so a
    question that already uses the book's register is unchanged. English terms
    are mapped the same way, so an English question reaches the Hindi corpus
    through the lexical half of the hybrid as well as the dense half.
    """
    extra: list[str] = []
    for term in tokens(question):
        for book_word in PARENT_TO_BOOK.get(term, ()):
            if book_word not in extra:
                extra.append(book_word)
    for term in _EN_TOKEN_RX.findall(question or ""):
        for book_word in ENGLISH_TO_BOOK.get(term.lower(), ()):
            if book_word not in extra:
                extra.append(book_word)
    return f"{question} {' '.join(extra)}".strip() if extra else question


class LexicalIndex:
    """IDF-weighted term overlap. Deliberately not full BM25: without document
    length normalisation tuning to justify, the simpler score is easier to reason
    about and behaves the same for these short queries."""

    def __init__(self, docs: list[str]) -> None:
        self.n = max(len(docs), 1)
        df: Counter[str] = Counter()
        self.doc_terms: list[set[str]] = []
        for d in docs:
            terms = set(tokens(d))
            self.doc_terms.append(terms)
            df.update(terms)
        self.idf = {
            t: math.log(1.0 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()
        }

    def score(self, query: str) -> list[float]:
        q_terms = [t for t in set(tokens(query)) if t not in STOPWORDS]
        if not q_terms:
            return [0.0] * len(self.doc_terms)
        weights = {t: self.idf.get(t, math.log(1.0 + self.n)) for t in q_terms}
        total = sum(weights.values()) or 1.0
        out = []
        for terms in self.doc_terms:
            hit = sum(w for t, w in weights.items() if t in terms)
            out.append(hit / total)  # 0..1, the share of query weight matched
        return out
