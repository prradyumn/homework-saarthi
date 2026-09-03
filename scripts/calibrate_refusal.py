"""Refusal calibration: sweep the gate and plot accuracy against coverage (PRD §8.1).

This calibrates the GATE, not the generator. §8.1's full curve needs generation,
which needs Groq; the decision this measures is narrower and comes first: given a
question, should we answer at all?

Labelling follows §8.1's asymmetry. Of the two error types:

  answering a should-refuse question   SERIOUS. The parent cannot detect it and
                                       may teach a child something false.
  refusing a should-answer question    mild. Honest, obvious, and recoverable,
                                       and the refusal ships a textbook page.

So the operating point is chosen as the **highest coverage whose
wrong-answer rate stays at or below 2%**, exactly as the PRD specifies.

Retrieval here uses the SAME scoring as production (D3): dense cosine plus an
IDF term-overlap signal, over a query expanded with the textbook's vocabulary.
The earlier 73% coverage ceiling was measured with dense-only scoring, so it was
a ceiling on the old retriever, not on the gate — the fix for it was always
better retrieval rather than a better threshold.

Signals per question (§8.1 step 2):
  top1        cosine similarity of the best chunk
  margin      top1 minus top5 — a foreign question sits roughly equidistant
              from everything, so its margin is small
  in_class5   whether the best chunk is Class 5 Maths rather than a decoy.
              Meaningless without the decoy corpus, which is why it exists
              (DECISIONS.md D1-PRELIM)

Usage: python scripts/calibrate_refusal.py [--embed]
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
sys.path.insert(0, str(ROOT / "eval"))

CHUNKS = ROOT / "ingest" / "chunks.json"
DECOYS = ROOT / "ingest" / "decoy_chunks.json"
INDEX = ROOT / "ingest" / "index_combined.npz"
QSET = ROOT / "eval" / "refusal_set.json"
REPORT = ROOT / "eval" / "refusal_calibration.json"
CURVE = ROOT / "eval" / "accuracy_vs_coverage.csv"

MODEL = "BAAI/bge-m3"
LEXICAL_WEIGHT = 0.35  # matches scripts/answer.py, so the gate is calibrated on
                       # the retriever that actually runs in production
# (min beyond-syllabus marker hits, min beyond:class5 hit ratio) — the decoy
# corpus is the binding constraint on coverage, so its definition is swept.
DECOY_FILTERS = [(1, 0.0), (1, 0.5), (2, 0.0), (2, 0.5), (2, 1.0), (3, 0.5)]
TOP_K = 5
WRONG_ANSWER_BUDGET = 0.02  # §7.3 guardrail: 2% maximum, non-negotiable


def embedding_text(c: dict) -> str:
    parts = [
        f"अध्याय {c['chapter']}: {c.get('chapter_title_hi', '')}",
        c.get("section_header_hi", ""),
        c["text_hi"],
    ]
    if c.get("fractions"):
        parts.append(" ".join(c["fractions"]))
    return "\n".join(p for p in parts if p)


def load_all_chunks() -> list[dict]:
    main = json.loads(CHUNKS.read_text(encoding="utf-8"))
    for c in main:
        c["in_syllabus"] = True
    decoys = json.loads(DECOYS.read_text(encoding="utf-8")) if DECOYS.exists() else []
    return main + decoys


def build_index() -> None:
    from sentence_transformers import SentenceTransformer

    chunks = load_all_chunks()
    model = SentenceTransformer(MODEL, device="cpu")
    vecs = model.encode(
        [embedding_text(c) for c in chunks], batch_size=4,
        normalize_embeddings=True, show_progress_bar=True, convert_to_numpy=True,
    )
    np.savez_compressed(
        INDEX, vectors=vecs.astype(np.float32),
        ids=np.array([c["id"] for c in chunks]),
    )
    n5 = sum(c["in_syllabus"] for c in chunks)
    print(f"  indexed {len(chunks)} chunks ({n5} class 5, {len(chunks) - n5} decoy)")


def signals() -> list[dict]:
    from sentence_transformers import SentenceTransformer

    from query_gate import has_beyond_class5_word, is_value_seeking, pre_check
    from retrieval import LexicalIndex, expand_query

    chunks = load_all_chunks()
    by_id = {c["id"]: c for c in chunks}
    data = np.load(INDEX, allow_pickle=False)
    vectors, ids = data["vectors"], [str(x) for x in data["ids"]]

    qs = json.loads(QSET.read_text(encoding="utf-8"))
    model = SentenceTransformer(MODEL, device="cpu")
    expanded = [expand_query(q["question_hi"]) for q in qs]
    qvecs = model.encode(
        expanded, batch_size=4, normalize_embeddings=True, convert_to_numpy=True,
    )
    lex = LexicalIndex([
        f"{by_id[i].get('section_header_hi', '')} {by_id[i]['text_hi']}" for i in ids
    ])

    rows = []
    for q, qv, qtext in zip(qs, qvecs, expanded):
        sims = vectors @ qv + LEXICAL_WEIGHT * np.asarray(
            lex.score(qtext), dtype=np.float32
        )
        order = np.argsort(-sims)[:TOP_K]
        top = [by_id[ids[i]] for i in order]
        scores = [float(sims[i]) for i in order]
        # Best score within each corpus, over the whole index. A decoy that wins
        # by a hair is not evidence a question is out of syllabus; the gap is.
        best_c5 = max((float(sims[i]) for i, cid in enumerate(ids)
                       if by_id[cid]["in_syllabus"]), default=0.0)
        best_decoy = max((float(sims[i]) for i, cid in enumerate(ids)
                          if not by_id[cid]["in_syllabus"]), default=0.0)
        # Best decoy score under each candidate density filter, so the decoy
        # corpus can be tuned without re-embedding. A decoy only "counts" if it
        # is predominantly beyond Class 5 by that setting's definition.
        best_decoy_at = {}
        for hits, ratio in DECOY_FILTERS:
            best_decoy_at[f"{hits}_{ratio:g}"] = max(
                (float(sims[i]) for i, cid in enumerate(ids)
                 if not by_id[cid]["in_syllabus"]
                 and by_id[cid].get("beyond_hits", 0) >= hits
                 and by_id[cid].get("beyond_hits", 0)
                 >= by_id[cid].get("class5_hits", 0) * ratio),
                default=0.0,
            )
        rows.append(
            {
                **q,
                "top1": round(scores[0], 4),
                "margin": round(scores[0] - scores[-1], 4),
                "in_class5": bool(top[0]["in_syllabus"]),
                "top1_class": top[0]["class"],
                "top1_chapter": top[0]["chapter"],
                "top1_header": top[0].get("section_header_hi", ""),
                "chapter_hit": (
                    top[0]["chapter"] == q.get("expected_chapter")
                    if q["label"] == "answer" and top[0]["in_syllabus"] else None
                ),
                "class5_in_topk": any(c["in_syllabus"] for c in top),
                "top1_figure_dependent": bool(top[0].get("figure_dependent")),
                "top1_needs_review": bool(top[0].get("needs_review")),
                "pre_check": pre_check(q["question_hi"]),
                "value_seeking": is_value_seeking(q["question_hi"]),
                "q_beyond_word": has_beyond_class5_word(q["question_hi"]),
                "top1_has_numbers": bool(re.search(r"\d", top[0]["text_hi"])),
                "top1_has_chart_axis": bool(top[0].get("has_chart_axis")),
                "best_class5": round(best_c5, 4),
                "best_decoy": round(best_decoy, 4),
                "decoy_lead": round(best_decoy - best_c5, 4),
                "best_decoy_at": {k: round(v, 4) for k, v in best_decoy_at.items()},
            }
        )
    return rows


def sweep(rows: list[dict]) -> list[dict]:
    """Coverage and wrong-answer rate at every threshold, for four gate designs."""
    should_answer = [r for r in rows if r["label"] == "answer"]
    should_refuse = [r for r in rows if r["label"] == "refuse"]

    designs = {
        "similarity_only": lambda r, t: r["top1"] >= t,
        "similarity_and_class5": lambda r, t: r["top1"] >= t and r["in_class5"],
        "class5_only": lambda r, _t: r["in_class5"],
        "similarity_class5_and_margin": (
            lambda r, t: r["top1"] >= t and r["in_class5"] and r["margin"] >= 0.03
        ),
        # The production gate. Adds the two ingest-time flags that already exist
        # on every chunk but were never wired into the gate: a chunk whose content
        # is carried by a figure cannot answer from text at ANY retrieval quality
        # (D0.1), and a chunk that failed the extraction gate should not be
        # answered from either.
        "production": (
            lambda r, t: (
                r["top1"] >= t
                and r["in_class5"]
                and not r["top1_figure_dependent"]
                and not r["top1_needs_review"]
            )
        ),
        # The layered gate: query-side pre-checks first, then metadata, then the
        # chunk flags, and only then a similarity floor on the residual. Each
        # refusal class is handled by the mechanism that can actually see it.
        "layered": (
            lambda r, t: (
                r["pre_check"]["outcome"] == "pass"
                and r["in_class5"]
                and not r["top1_needs_review"]
                and r["top1"] >= t
            )
        ),
        # Adds the numeric-answerability check: a question asking for a value,
        # whose best chunk states no numbers at all, is asking for something that
        # lives in a figure. Cheap, general, and no model.
        "layered_plus_numeric": (
            lambda r, t: (
                r["pre_check"]["outcome"] == "pass"
                and r["in_class5"]
                and not r["top1_needs_review"]
                and not (r["value_seeking"] and not r["top1_has_numbers"])
                and not (r["value_seeking"] and r["top1_has_chart_axis"])
                and r["top1"] >= t
            )
        ),
    }

    # Read the QUESTION's vocabulary instead of comparing corpora. No decoys, no
    # margin, no threshold on the decoy side — just "does the question name a
    # topic the Class 5 book does not contain".
    designs["topic_first"] = (
        lambda r, t: (
            r["pre_check"]["outcome"] == "pass"
            and not r["q_beyond_word"]
            and not r["top1_needs_review"]
            and not (r["value_seeking"] and r["top1_has_chart_axis"])
            and not (r["value_seeking"] and not r["top1_has_numbers"])
            and r["best_class5"] >= t
        )
    )
    # Question vocabulary catches 17 of 20 out-of-syllabus maths questions with
    # ZERO false positives on the 100 legitimate ones — but the 3 it misses are
    # enough to force the threshold up on its own, so it is combined with the
    # decoy corpus rather than replacing it. Vocabulary handles the clear cases
    # categorically; the decoy margin backstops the vocabulary's blind spots, and
    # can therefore be looser than it could be alone.
    for gap in (0.01, 0.03, 0.05, 0.08, 0.12, 0.18, 0.25):
        designs[f"topic+decoy_{gap:g}"] = (
            lambda r, t, g=gap: (
                r["pre_check"]["outcome"] == "pass"
                and not r["q_beyond_word"]
                and r["decoy_lead"] < g
                and not r["top1_needs_review"]
                and not (r["value_seeking"] and r["top1_has_chart_axis"])
                and not (r["value_seeking"] and not r["top1_has_numbers"])
                and r["best_class5"] >= t
            )
        )

    # Sweep the DECOY CORPUS DEFINITION. Thinning it recovers legitimate questions
    # (79% -> 98% reached a Class 5 chunk) but guts out-of-syllabus detection
    # (28% -> 52% of refusables wrongly reached Class 5), collapsing coverage from
    # 71% to 15%. The corpus size trades one against the other, so the setting is
    # an empirical question, measured here from one embedding run.
    for hits, ratio in DECOY_FILTERS:
        key = f"{hits}_{ratio:g}"
        designs[f"decoy_{key}"] = (
            lambda r, t, k=key: (
                r["pre_check"]["outcome"] == "pass"
                and r["best_class5"] >= r["best_decoy_at"][k]
                and not r["top1_needs_review"]
                and not (r["value_seeking"] and r["top1_has_chart_axis"])
                and not (r["value_seeking"] and not r["top1_has_numbers"])
                and r["best_class5"] >= t
            )
        )

    # The decoy signal as a MARGIN rather than a rank. Requiring the decoy to beat
    # the best Class 5 chunk by a gap, instead of merely ranking first, is the
    # lever on the remaining constraint: 21 of 100 legitimate questions still had
    # a decoy as their nearest neighbour, which caps coverage at 79% before any
    # threshold is applied. Swept, because the right gap is an empirical question.
    for gap in (0.07, 0.08, 0.09, 0.10, 0.12, 0.15, 0.20, 0.30):
        designs[f"margin_{gap:g}"] = (
            lambda r, t, g=gap: (
                r["pre_check"]["outcome"] == "pass"
                and r["decoy_lead"] < g
                and not r["top1_needs_review"]
                and not (r["value_seeking"] and r["top1_has_chart_axis"])
                and not (r["value_seeking"] and not r["top1_has_numbers"])
                and max(r["best_class5"], 0.0) >= t
            )
        )

    out = []
    for name, rule in designs.items():
        thresholds = [round(x, 3) for x in np.arange(0.30, 0.78, 0.005)]
        if name == "class5_only":
            thresholds = [0.0]
        for t in thresholds:
            answered_ok = [r for r in should_answer if rule(r, t)]
            answered_bad = [r for r in should_refuse if rule(r, t)]
            n_answered = len(answered_ok) + len(answered_bad)
            coverage = len(answered_ok) / len(should_answer)
            wrong_rate = len(answered_bad) / max(n_answered, 1)
            out.append(
                {
                    "design": name, "threshold": t,
                    "coverage_of_answerable": round(coverage, 4),
                    "wrong_answer_rate": round(wrong_rate, 4),
                    "answered_total": n_answered,
                    "answered_correctly_allowed": len(answered_ok),
                    "answered_should_have_refused": len(answered_bad),
                    "refusal_rate_overall": round(1 - n_answered / len(rows), 4),
                }
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed", action="store_true")
    args = ap.parse_args()
    if args.embed or not INDEX.exists():
        build_index()

    rows = signals()
    curve = sweep(rows)

    # --- signal separation, the D1-PRELIM check at full scale ---
    ans = [r for r in rows if r["label"] == "answer"]
    ref = [r for r in rows if r["label"] == "refuse"]
    print(f"\n  {'set':28}{'n':>4}{'top1 min':>10}{'mean':>8}{'max':>8}{'margin mean':>13}")
    print("  " + "-" * 71)
    for name, group in (("should answer", ans), ("should refuse", ref)):
        t = [r["top1"] for r in group]
        m = [r["margin"] for r in group]
        print(
            f"  {name:28}{len(group):>4}{min(t):>10.3f}{sum(t) / len(t):>8.3f}"
            f"{max(t):>8.3f}{sum(m) / len(m):>13.3f}"
        )
    print(f"\n  overlap: lowest answerable {min(r['top1'] for r in ans):.3f} "
          f"vs highest refusable {max(r['top1'] for r in ref):.3f}")

    # --- how well does the decoy metadata signal work on its own? ---
    print(f"\n  decoy signal (top-1 chunk is Class 5):")
    print(f"    of {len(ans)} answerable: {sum(r['in_class5'] for r in ans)} "
          f"({sum(r['in_class5'] for r in ans) / len(ans):.0%}) hit a Class 5 chunk")
    print(f"    of {len(ref)} refusable:  {sum(r['in_class5'] for r in ref)} "
          f"({sum(r['in_class5'] for r in ref) / len(ref):.0%}) wrongly hit a Class 5 chunk")

    # --- the operating point per design ---
    print(f"\n  {'design':30}{'thr':>7}{'coverage':>10}{'wrong':>8}{'refusal':>9}")
    print("  " + "-" * 64)
    best = {}
    for name in dict.fromkeys(r["design"] for r in curve):
        pts = [r for r in curve if r["design"] == name]
        ok = [p for p in pts if p["wrong_answer_rate"] <= WRONG_ANSWER_BUDGET]
        pick = max(ok, key=lambda p: p["coverage_of_answerable"]) if ok else None
        best[name] = pick
        if pick:
            print(
                f"  {name:30}{pick['threshold']:>7.3f}"
                f"{pick['coverage_of_answerable']:>10.1%}"
                f"{pick['wrong_answer_rate']:>8.1%}{pick['refusal_rate_overall']:>9.1%}"
            )
        else:
            floor = min(p["wrong_answer_rate"] for p in pts)
            print(f"  {name:30}{'—':>7}{'—':>10}{'—':>8}   cannot reach 2% "
                  f"(floor {floor:.1%})")

    CURVE.write_text(
        "design,threshold,coverage,wrong_answer_rate,refusal_rate\n"
        + "\n".join(
            f"{r['design']},{r['threshold']},{r['coverage_of_answerable']},"
            f"{r['wrong_answer_rate']},{r['refusal_rate_overall']}"
            for r in curve
        ),
        encoding="utf-8",
    )
    REPORT.write_text(
        json.dumps(
            {"model": MODEL, "top_k": TOP_K, "budget": WRONG_ANSWER_BUDGET,
             "operating_points": best, "signals": rows},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  curve -> {CURVE}\n  report -> {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
