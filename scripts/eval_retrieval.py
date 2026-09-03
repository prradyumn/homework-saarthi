"""Chunk-level retrieval quality, measured without calling any LLM.

Chapter-level accuracy (97% @5) turned out to be far too coarse a metric: it was
satisfied while a third of questions received the wrong chunk *within* the right
chapter, which showed up downstream as the generator correctly declining because
its passages did not contain the answer.

This measures the thing that actually matters to generation — does the context we
hand the model contain the concept the parent asked about — and it needs no API
budget, so it can be run when the daily token cap is spent.

Metric. For each question, a distinctive content term is derived from the question
itself: its tokens minus scaffolding stopwords, keeping the rarest by corpus IDF.
A retrieval is a hit if any of the top-k chunks contains that term **or the
textbook's word for it** (retrieval.PARENT_TO_BOOK).

That last clause matters. Scoring only the question's own words measured 87%
before query expansion and 83% after — punishing the fix for working, because a
question about a "नक्शा" should now retrieve a chapter that says "मानचित्र". A
metric has to accept the substitution it asked for.

This is a proxy for relevance and is honest about being one, but it correlates
directly with whether the generator can answer, which chapter-level accuracy did
not.

Usage: python scripts/eval_retrieval.py [--k 3]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from retrieval import (  # noqa: E402
    PARENT_TO_BOOK,
    STOPWORDS,
    LexicalIndex,
    expand_query,
    tokens,
)

CHUNKS = ROOT / "ingest" / "chunks.json"
DECOYS = ROOT / "ingest" / "decoy_chunks.json"
INDEX = ROOT / "ingest" / "index_combined.npz"
QSET = ROOT / "eval" / "refusal_set.json"
OUT = ROOT / "eval" / "retrieval_chunklevel.json"


def key_terms(question: str, idf: dict[str, float], n: int = 2) -> list[str]:
    """The rarest non-scaffolding terms in the question, plus the textbook's word
    for any of them, since either is evidence the right passage was found."""
    cand = [t for t in dict.fromkeys(tokens(question)) if t not in STOPWORDS and len(t) > 2]
    cand.sort(key=lambda t: -idf.get(t, 99.0))
    picked = cand[:n]
    accepted = list(picked)
    for t in picked:
        for book_word in PARENT_TO_BOOK.get(t, ()):
            if book_word not in accepted:
                accepted.append(book_word)
    return accepted


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()

    from sentence_transformers import SentenceTransformer

    from eval_generation import stratified_questions

    main_chunks = json.loads(CHUNKS.read_text(encoding="utf-8"))
    for c in main_chunks:
        c["in_syllabus"] = True
    decoys = json.loads(DECOYS.read_text(encoding="utf-8")) if DECOYS.exists() else []
    by_id = {c["id"]: c for c in main_chunks + decoys}

    data = np.load(INDEX, allow_pickle=False)
    vectors, ids = data["vectors"], [str(x) for x in data["ids"]]
    docs = [f"{by_id[i].get('section_header_hi', '')} {by_id[i]['text_hi']}" for i in ids]
    lex = LexicalIndex(docs)

    questions = stratified_questions(args.n)
    model = SentenceTransformer("BAAI/bge-m3", device="cpu")
    qvecs = model.encode([expand_query(q["question_hi"]) for q in questions],
                         batch_size=4, normalize_embeddings=True,
                         convert_to_numpy=True)

    # does the term appear anywhere in the Class 5 corpus at all? if not, no
    # retrieval setting can succeed and the question is figure-dependent (D0.1)
    c5_text = " ".join(f"{c['section_header_hi']} {c['text_hi']}" for c in main_chunks)

    weights = [0.0, 0.2, 0.35, 0.5, 0.8]
    results: dict[str, dict] = {}
    per_q: list[dict] = []

    for w in weights:
        hits = 0
        unanswerable = 0
        rows = []
        for q, qv in zip(questions, qvecs):
            terms = key_terms(q["question_hi"], lex.idf)
            dense = vectors @ qv
            score = dense + w * np.asarray(
                lex.score(expand_query(q["question_hi"])), dtype=np.float32)
            order = np.argsort(-score)[: args.k]
            top = [by_id[ids[i]] for i in order]
            ctx = " ".join(f"{c.get('section_header_hi','')} {c['text_hi']}" for c in top)
            present = any(re.search(rf"(?<![ऀ-ॿ]){re.escape(t)}", ctx) for t in terms)
            in_corpus = any(
                re.search(rf"(?<![ऀ-ॿ]){re.escape(t)}", c5_text) for t in terms
            )
            hits += present
            unanswerable += not in_corpus
            rows.append({"id": q["id"], "question_hi": q["question_hi"],
                         "key_terms": terms, "term_in_context": present,
                         "term_in_corpus": in_corpus,
                         "top_chapters": [c["chapter"] for c in top]})
        results[f"lexical_{w:g}"] = {
            "term_in_context": hits / len(questions),
            "questions_whose_term_is_absent_from_corpus": unanswerable,
        }
        if abs(w - 0.35) < 1e-9:
            per_q = rows

    print(f"  chunk-level relevance, top-{args.k}, n={len(questions)}\n")
    print(f"  {'lexical weight':18}{'term found in context':>24}")
    print("  " + "-" * 42)
    for name, r in results.items():
        w = name.split("_")[1]
        tag = "  <- dense only" if w == "0" else ("  <- in use" if w == "0.35" else "")
        print(f"  {w:18}{r['term_in_context']:>23.0%}{tag}")

    absent = results["lexical_0.35"]["questions_whose_term_is_absent_from_corpus"]
    print(f"\n  questions whose key term appears NOWHERE in the Class 5 corpus: "
          f"{absent}/{len(questions)}")
    print("  (figure-dependent — no retrieval setting can reach these, D0.1)")
    if per_q:
        print("\n  still missed at the weight in use:")
        for r in per_q:
            if not r["term_in_context"]:
                mark = "corpus-absent" if not r["term_in_corpus"] else "RETRIEVAL MISS"
                print(f"     [{mark:14}] {r['key_terms']}  {r['question_hi'][:44]}")

    OUT.write_text(json.dumps({"k": args.k, "n": len(questions),
                               "by_weight": results, "detail": per_q},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
