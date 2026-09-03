"""Why does the retrieved passage not cover the question?

The v6 conformance run put the bottleneck beyond doubt: 23 questions, **1**
contract failure and **9** gate refusals, six of them
`passage_does_not_cover_question`. Generation is not the problem. And in five of
those six the cited chapter was the *expected* chapter — so retrieval found the
right chapter and the wrong concept-unit inside it.

That leaves two hypotheses, and both are answerable with no LLM call at all:

  H1  the covering chunk exists but ranks below CONTEXT_CHUNKS (=3), so the
      generator never sees it
  H2  the covering chunk IS in context but CONTEXT_CHARS (=900) truncates it
      before the part that answers the question

This prints, for each question, every Class 5 chunk in the expected chapter with
its score and rank, marks which three reach the generator, and flags any chunk
the 900-character window would cut. Judgement about which chunk *covers* the
question is left to a human reading the output — inferring it from the score
would assume the very thing under test.

Costs nothing to run: retrieval is local, and the Groq daily budget is not
touched.

  python scripts/diagnose_coverage.py                 # the v6 failures
  python scripts/diagnose_coverage.py --all           # every golden question
  python scripts/diagnose_coverage.py --q "..."       # one ad-hoc question
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from answer import (  # noqa: E402
    CONTEXT_CHARS,
    CONTEXT_CHUNKS,
    load_chunks,
    retrieve,
)
from retrieval import STOPWORDS, expand_query  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
# The conformance eval draws from the refusal set's in-syllabus "answer"
# questions, not from golden_seed.json — the ids are rs-in-*, and pointing
# this at the wrong file silently reported 0 questions.
QSET = ROOT / "eval" / "refusal_set.json"
V6 = ROOT / "eval" / "generation_groq_v6_final.json"
OUT = ROOT / "eval" / "coverage_diagnosis.json"


# Needles authored by reading the chapter, one set per failing question: text
# that a passage MUST contain to be capable of answering it. This is the weak
# link in the measurement — it is my reading of the book, not ground truth — so
# it is written down here to be argued with rather than buried in a number.
# It predicts only whether answer-bearing text reaches the generator, never
# whether the generator then writes a conforming answer.
NEEDLES = {
    "rs-in-017": ["समकोण", "एक-चौथाई"],          # what is a right angle (ch3)
    "rs-in-023": ["व्यवकलन", "घटा"],              # subtracting large numbers (ch4)
    "rs-in-024": ["हासिल", "योग"],                # carrying in addition (ch4)
    "rs-in-037": ["शून्य", "गुणा"],               # multiplying by 10 and 100 (ch6)
    "rs-in-045": ["पंचभुज", "टाइल"],              # why regular pentagons don't tile (ch7)
    "rs-in-053": ["धारिता", "लीटर"],              # measuring capacity (ch8)
    "rs-in-072": ["क्षेत्रफल", "टाइल"],           # arranging shapes in a quilt (ch11)
    "rs-in-084": ["गुणज", "छलाँग"],               # multiples in the animal-jumps ch (ch13)
}


def needle_depth(label: str, ranked) -> tuple[int | None, int | None]:
    """Rank at which a chunk containing any / all needles first appears."""
    needles = NEEDLES.get(label)
    if not needles:
        return None, None
    any_at = all_at = None
    for rank, (_sc, c) in enumerate(ranked, 1):
        text = c.get("text_hi") or ""
        hits = [n for n in needles if n in text]
        if hits and any_at is None:
            any_at = rank
        if len(hits) == len(needles) and all_at is None:
            all_at = rank
    return any_at, all_at


def failing_ids() -> list[str]:
    """The questions v6 refused for a coverage or chart reason."""
    rows = json.loads(V6.read_text(encoding="utf-8"))["rows"]
    return [r["id"] for r in rows
            if any("passage_does_not_cover" in str(f) or "chart" in str(f)
                   for f in (r.get("failures") or []))]


def question_rows() -> list[dict]:
    g = json.loads(QSET.read_text(encoding="utf-8"))
    rows = g["questions"] if isinstance(g, dict) else g
    return [r for r in rows if r.get("label") == "answer"]


def report(question: str, expected_chapter, label: str = "") -> dict:
    hits, corpora = retrieve(question)
    by_id = load_chunks()

    # what the generator actually receives
    context_ids = [c["id"] for _, c in corpora["class5_context"]]

    # every scored Class 5 chunk, so a chunk sitting at rank 7 is visible
    ranked = corpora["class5_ranked"][:12]

    # H2, measured rather than assumed: where do the question's own terms sit
    # inside each context chunk? If a term appears ONLY beyond CONTEXT_CHARS then
    # the generator provably never saw it, and truncation is the cause rather
    # than a suspect.
    # Strip punctuation BEFORE the stopword test. Without this the only "lost"
    # terms reported were "है?" and "हैं?" — stopwords that STOPWORDS missed
    # because the question mark was still attached, so the H2 test was measuring
    # punctuation rather than content.
    terms = sorted({
        w for w in (t.strip("?।,.!\u0964:;\"'()") for t in expand_query(question).split())
        if w and w not in STOPWORDS and len(w) > 2})

    print(f"\n{'=' * 78}\n{label}  ch{expected_chapter}\n  Q: {question}")
    print(f"  {'-' * 74}")
    in_expected = 0
    rows = []
    for rank, (sc, c) in enumerate(ranked, 1):
        same_ch = str(c.get("chapter")) == str(expected_chapter)
        in_ctx = c["id"] in context_ids
        text = c.get("text_hi") or ""
        cut = len(text) > CONTEXT_CHARS
        if same_ch:
            in_expected += 1
        mark = "CTX" if in_ctx else "   "
        star = "*" if same_ch else " "
        print(f"  {mark} {star}#{rank:<2} {sc:.3f}  ch{str(c.get('chapter')):>2} "
              f"p{c.get('page')}  {len(text):>4}c"
              f"{'  TRUNC' if cut and in_ctx else '       '}  "
              f"{(c.get('section_header_hi') or c.get('concept_tag') or '')[:34]}")
        lost = sorted({t for t in terms
                       if t in text and text.index(t) >= CONTEXT_CHARS
                       and t not in text[:CONTEXT_CHARS]})
        if in_ctx and lost:
            print(f"        ^ beyond the {CONTEXT_CHARS}-char window, so unseen: "
                  f"{' '.join(lost)}")
        rows.append({"terms_lost_to_truncation": lost,
                     "rank": rank, "score": round(sc, 4),
                     "chunk_id": c["id"], "chapter": c.get("chapter"),
                     "page": c.get("page"), "chars": len(text),
                     "in_context": in_ctx, "expected_chapter": same_ch,
                     "truncated_in_context": bool(cut and in_ctx),
                     "section_header_hi": c.get("section_header_hi") or "",
                     "concept_tag": c.get("concept_tag") or "",
                     "figure_dependent": bool(c.get("figure_dependent")),
                     "has_chart_axis": bool(c.get("has_chart_axis"))})

    any_at, all_at = needle_depth(label, corpora["class5_ranked"])
    if any_at is not None:
        need = all_at or any_at
        verdict_depth = ("already in context" if need <= CONTEXT_CHUNKS
                         else f"needs CONTEXT_CHUNKS >= {need}")
        print(f"  -> answer-bearing text: any needle @#{any_at}, "
              f"all needles @{('#' + str(all_at)) if all_at else 'never'}"
              f"   ({verdict_depth})")
    lost_any = sorted({t for r in rows if r["in_context"]
                       for t in r["terms_lost_to_truncation"]})
    ctx_from_expected = sum(
        1 for r in rows if r["in_context"] and r["expected_chapter"])
    first_expected = next((r["rank"] for r in rows if r["expected_chapter"]), None)
    verdict = (
        "no chunk from the expected chapter was retrieved at all"
        if first_expected is None else
        f"H1: expected-chapter chunks exist but {ctx_from_expected}/{CONTEXT_CHUNKS} "
        f"reach the generator (first at rank {first_expected})"
        if ctx_from_expected < CONTEXT_CHUNKS and first_expected > CONTEXT_CHUNKS else
        "expected chapter fills the context — look at H2 (truncation) and at the text"
    )
    print(f"  -> {verdict}")
    return {"question": question, "label": label,
            "expected_chapter": expected_chapter,
            "first_expected_rank": first_expected,
            "context_from_expected": ctx_from_expected,
            "truncated": sum(1 for r in rows if r["truncated_in_context"]),
            "terms_lost_to_truncation": lost_any,
            "needle_any_rank": any_at, "needle_all_rank": all_at,
            "verdict": verdict, "ranked": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every in-syllabus question")
    ap.add_argument("--q", help="one ad-hoc question")
    ap.add_argument("--chapter", type=int, default=0, help="expected chapter for --q")
    args = ap.parse_args()

    print(f"CONTEXT_CHUNKS={CONTEXT_CHUNKS}  CONTEXT_CHARS={CONTEXT_CHARS}"
          f"   (no LLM calls; costs no Groq budget)")

    if args.q:
        out = [report(args.q, args.chapter or "?", "ad-hoc")]
    else:
        rows = question_rows()
        wanted = None if args.all else set(failing_ids())
        out = [report(r["question_hi"], r.get("expected_chapter"), r["id"])
               for r in rows if wanted is None or r["id"] in wanted]

    # the summary that decides what to change
    h1 = [o for o in out if o["first_expected_rank"] is not None
          and o["context_from_expected"] < CONTEXT_CHUNKS]
    missing = [o for o in out if o["first_expected_rank"] is None]
    trunc = [o for o in out if o["truncated"]]
    lost = [o for o in out if o["terms_lost_to_truncation"]]
    graded = [o for o in out if o["needle_any_rank"]]
    print(f"\n{'=' * 78}\nSUMMARY over {len(out)} question(s)")
    print(f"  expected chapter never retrieved   {len(missing)}")
    print(f"  H1 covering chunk ranked out       {len(h1)}"
          + (f"  (first at ranks {[o['first_expected_rank'] for o in h1]})" if h1 else ""))
    print(f"  H2 a context chunk was truncated   {len(trunc)}")
    print(f"  H2 CONFIRMED: a query term fell past the window   {len(lost)}")
    for o in lost:
        print(f"       {o['label']}: {' '.join(o['terms_lost_to_truncation'])}")
    if graded:
        print(f"\n  Predicted gain from depth alone (needles, no LLM):")
        for k in (3, 4, 5, 6, 8):
            got = sum(1 for o in graded
                      if (o["needle_all_rank"] or o["needle_any_rank"]) <= k)
            print(f"    CONTEXT_CHUNKS={k}:  {got}/{len(graded)} failures would "
                  f"receive answer-bearing text")
        print("  This bounds what depth CAN fix. It does not promise conformance:\n"
              "  receiving the passage and writing a conforming answer are different\n"
              "  things, and only eval_generation.py measures the second.")
    deepest = max((o["first_expected_rank"] or 0) for o in out) if out else 0
    if deepest > CONTEXT_CHUNKS:
        print(f"\n  CONTEXT_CHUNKS would need to be {deepest} to include every "
              f"expected-chapter chunk. Raising it costs tokens per question and "
              f"the daily budget is already the binding constraint — measure the "
              f"conformance gain before spending it.")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
