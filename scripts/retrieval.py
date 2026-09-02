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
