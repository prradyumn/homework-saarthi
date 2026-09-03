"""What does widening the context window cost, in answers per day?

The free tier's binding constraint is not money, it is 200,000 tokens per day —
and a measured answer costs ~2,212 of them (eval/measured_tokens.json), so the
whole project gets about **90 answers a day**. A 30-question conformance run
spends a third of that.

Two knobs could raise conformance, and this prices both, because at 90 answers a
day an experiment that costs a quarter of daily capacity is a decision rather
than a detail.

CONTEXT_CHARS=900 withholds 20% of the book's in-syllabus prose: 49% of chunks
are longer than the cap (median 1340, p90 1437, max 1548). That looked like the
answer until it was tested — in ch11 every area and perimeter formula sits INSIDE
the window, so truncation hides nothing there.

CONTEXT_CHUNKS=3 is the live suspect. Those same ch11 formula-bearing chunks
ranked #4 and #9, so the generator never received them. Depth, not length.

No LLM calls: the prompts are built locally and converted to tokens with the
chars-per-token ratio derived from Groq's own usage block on a real request.

  python scripts/window_cost.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from answer import (  # noqa: E402
    CONTEXT_CHUNKS,
    SYSTEM_PROMPT,
    USER_TEMPLATE,
    retrieve,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
QSET = ROOT / "eval" / "refusal_set.json"
MEASURED = ROOT / "eval" / "measured_tokens.json"
OUT = ROOT / "eval" / "window_cost.json"

TPD = 200_000                     # free tier, per organisation, per day
WINDOWS = (900, 1100, 1300, 1550)  # 1550 clears the longest chunk (1548)
# CONTEXT_CHUNKS matters more than CONTEXT_CHARS, and pricing only the latter was
# a mistake. In ch11 every area/perimeter formula sits INSIDE the 900-char window
# — truncation hides nothing there — but the chunks carrying them ranked #4 and #9
# while only the top 3 reach the generator. Depth, not length, is the lever.
CHUNK_COUNTS = (3, 4, 5, 6)
N_QUESTIONS = 30                   # the conformance run
SAMPLE = 12                        # prompts built, to keep this quick


def build_prompt(question: str, chars: int, chunks: int) -> str:
    _hits, corpora = retrieve(question)
    passages = corpora["class5_ranked"][:chunks]
    context = "\n\n".join(
        f"[अध्याय {c['chapter']} — {c['chapter_title_hi']}, पेज {c['page']}]\n"
        f"{c['text_hi'][:chars]}"
        for _sc, c in passages
    )
    top = passages[0][1] if passages else {"chapter_title_hi": ""}
    return SYSTEM_PROMPT + USER_TEMPLATE.format(
        title=top["chapter_title_hi"], context=context, question=question)


def main() -> int:
    rows = json.loads(QSET.read_text(encoding="utf-8"))
    rows = [r for r in (rows["questions"] if isinstance(rows, dict) else rows)
            if r.get("label") == "answer"][:SAMPLE]

    measured = json.loads(MEASURED.read_text(encoding="utf-8"))
    obs = measured["observations"]
    prompt_tokens = sum(o["prompt_tokens"] for o in obs) / len(obs)
    completion_tokens = sum(o["completion_tokens"] for o in obs) / len(obs)

    # Calibrate chars->tokens on the SAME configuration those observations were
    # taken under (the 900-char window), so the ratio is measured rather than
    # guessed at from a general rule about Devanagari.
    base_chars = sum(len(build_prompt(r["question_hi"], 900, CONTEXT_CHUNKS))
                     for r in rows) / len(rows)
    chars_per_token = base_chars / prompt_tokens
    print(f"  calibration: {base_chars:.0f} prompt chars <-> {prompt_tokens:.0f} "
          f"prompt tokens  =  {chars_per_token:.2f} chars/token")
    print(f"  completion:  {completion_tokens:.0f} tokens (unchanged by the window)")
    print(f"\n  {'window':>7}  {'chars':>7}  {'tokens':>7}  {'answers/day':>11}  "
          f"{'30-Q run':>9}  {'runs/day':>8}")

    out = []
    for w in WINDOWS:
        chars = sum(len(build_prompt(r["question_hi"], w, CONTEXT_CHUNKS))
                    for r in rows) / len(rows)
        total = chars / chars_per_token + completion_tokens
        per_day = int(TPD / total)
        run = total * N_QUESTIONS
        print(f"  {w:>7}  {chars:>7.0f}  {total:>7.0f}  {per_day:>11}  "
              f"{run:>9.0f}  {TPD / run:>8.1f}")
        out.append({"context_chars": w, "prompt_chars": round(chars),
                    "tokens_per_answer": round(total),
                    "answers_per_day": per_day,
                    "tokens_per_30q_run": round(run),
                    "runs_per_day": round(TPD / run, 1)})

    print(f"\n  {'chunks':>7}  {'chars':>7}  {'tokens':>7}  {'answers/day':>11}  "
          f"{'30-Q run':>9}  {'runs/day':>8}   (window fixed at 900)")
    depth = []
    for k in CHUNK_COUNTS:
        chars = sum(len(build_prompt(r["question_hi"], 900, k)) for r in rows) / len(rows)
        total = chars / chars_per_token + completion_tokens
        run = total * N_QUESTIONS
        print(f"  {k:>7}  {chars:>7.0f}  {total:>7.0f}  {int(TPD / total):>11}  "
              f"{run:>9.0f}  {TPD / run:>8.1f}")
        depth.append({"context_chunks": k, "prompt_chars": round(chars),
                      "tokens_per_answer": round(total),
                      "answers_per_day": int(TPD / total),
                      "tokens_per_30q_run": round(run),
                      "runs_per_day": round(TPD / run, 1)})

    base, wide = out[0], out[-1]
    lost = base["answers_per_day"] - wide["answers_per_day"]
    print(f"\n  Going from {base['context_chars']} to {wide['context_chars']} chars "
          f"costs {lost} answers/day ({100 * lost / base['answers_per_day']:.0f}%) "
          f"and takes a 30-Q run from\n  {base['tokens_per_30q_run']:,} to "
          f"{wide['tokens_per_30q_run']:,} tokens — "
          f"{wide['runs_per_day']:.1f} runs/day instead of "
          f"{base['runs_per_day']:.1f}.")
    d3, d5 = depth[0], depth[2]
    print(f"\n  Going from {d3['context_chunks']} to {d5['context_chunks']} chunks "
          f"costs {d3['answers_per_day'] - d5['answers_per_day']} answers/day "
          f"({100 * (d3['answers_per_day'] - d5['answers_per_day']) / d3['answers_per_day']:.0f}%).")
    print("\n  Neither is known to BUY anything. Two runs on a fresh budget, "
          "depth first\n  because the ch11 evidence points there:")
    print("    SAATHI_CONTEXT_CHUNKS=5 python scripts/eval_generation.py groq "
          "--n 30 --tag k5")
    print("    SAATHI_CONTEXT_CHARS=1550 python scripts/eval_generation.py groq "
          "--n 30 --tag w1550")
    print("  Compare against v6's 56.5%. If neither moves, the ceiling is the "
          "textbook,\n  not the window: some questions ask about topics the book "
          "teaches only as an\n  activity, and no passage states them.")

    OUT.write_text(json.dumps(
        {"tpd": TPD, "chars_per_token": round(chars_per_token, 3),
         "completion_tokens": round(completion_tokens),
         "context_chunks": CONTEXT_CHUNKS, "sample_questions": len(rows),
         "windows": out, "depths": depth}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n  -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
