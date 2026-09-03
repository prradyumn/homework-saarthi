"""Measure answer-contract conformance for a generation backend (PRD §12.1).

Track A asserts contract conformance — all four parts present, and part 3's
different-numbers rule — alongside answer accuracy. Conformance is the half that
can be measured without a human, so it is measured here. The bar to ship is 100%
contract conformance (§12.1); anything less is the gap to close.

Questions are drawn from the labelled set's in-syllabus half and **stratified by
chapter**, because an 8-question ad-hoc list was too small to tell a real change
from noise, and it under-sampled the thin figure-heavy chapters where generation
has least to work with.

The number that matters for the tiering decision (DECISIONS.md D4): a backend that
cannot clear the contract does not produce wrong answers, it produces refusals.
That is safe but useless, and it is worth knowing which.

Usage:
  python scripts/eval_generation.py groq            # 30 questions, 2 per chapter
  python scripts/eval_generation.py groq --n 45
  python scripts/eval_generation.py ollama --n 8
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from answer import RateLimited, RateLimitExhausted, answer  # noqa: E402

QSET = ROOT / "eval" / "refusal_set.json"

# Free-tier limit read off the live response headers: 8,000 tokens per MINUTE
# (not requests per day, as the PRD assumed). At ~1,800 tokens per call including
# the retry that is ~4/min, so calls are paced rather than eating 429s.
GROQ_PACE_SECONDS = 15.0


def stratified_questions(n: int) -> list[dict]:
    """Even coverage across chapters, deterministic, no random seed to remember."""
    qs = [q for q in json.loads(QSET.read_text(encoding="utf-8")) if q["label"] == "answer"]
    by_chapter: dict[int, list[dict]] = defaultdict(list)
    for q in qs:
        by_chapter[q["expected_chapter"]].append(q)
    picked: list[dict] = []
    round_no = 0
    while len(picked) < n:
        added = False
        for ch in sorted(by_chapter):
            if round_no < len(by_chapter[ch]) and len(picked) < n:
                picked.append(by_chapter[ch][round_no])
                added = True
        if not added:
            break
        round_no += 1
    return picked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("backend", nargs="?", default="groq", choices=["groq", "gemini", "ollama"])
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--tag", default="", help="label this run in the output filename")
    args = ap.parse_args()

    questions = stratified_questions(args.n)
    pace = GROQ_PACE_SECONDS if args.backend == "groq" else 0.0
    rows, errors, t0 = [], [], time.time()

    for i, q in enumerate(questions):
        if i and pace:
            time.sleep(pace)
        try:
            try:
                out = answer(q["question_hi"], backend=args.backend, verbose=False)
            except RateLimited as exc:
                # per-minute cap: wait it out once rather than record a failure
                print(f"  ... per-minute limit hit, pausing 30s", flush=True)
                time.sleep(30)
                out = answer(q["question_hi"], backend=args.backend, verbose=False)
        except RateLimitExhausted as exc:
            print(f"\n  ABORTING at {i}/{len(questions)}: {exc}")
            print("  Partial results are NOT a conformance score — rerun after reset.")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  ERR  ch{q['expected_chapter']:>2}  {q['question_hi'][:40]:42} {exc}",
                  flush=True)
            # An infrastructure failure is NOT evidence about the model, and
            # folding it into the denominator produces a number that looks like a
            # conformance score and is not one. A DNS outage mid-run once
            # reported "27%" when 18 of 30 questions never reached the API.
            errors.append({**q, "error": str(exc)})
            continue

        answered = out.get("answered", False)
        codes: list[str] = []
        if not answered:
            if out.get("attempts"):
                codes = [f["code"] for f in out["attempts"][-1]["validation"]["failures"]]
            else:
                codes = [f"gate:{out['refusal']['reason']}"]
        rows.append({
            **q,
            "answered": answered,
            "retried": out.get("retried"),
            "seconds": out.get("seconds"),
            "failures": codes,
            "words": (out.get("stats") or {}).get("words"),
            "cited_chapter": (out.get("citation") or {}).get("chapter"),
        })
        flag = "OK " if answered else "REF"
        cite = out.get("citation") or {}
        hit = "" if not answered else (
            "" if cite.get("chapter") == q["expected_chapter"] else
            f" [cited ch{cite.get('chapter')}, expected ch{q['expected_chapter']}]"
        )
        print(f"  {flag} {out.get('seconds', 0):>6.1f}s ch{q['expected_chapter']:>2}  "
              f"{q['question_hi'][:38]:40} {'' if answered else codes}{hit}", flush=True)

    n = len(rows)
    if errors:
        print(f"\n  {len(errors)} question(s) never reached the API "
              f"(network/transport, not the model):")
        for e in errors[:4]:
            print(f"     ch{e['expected_chapter']:>2}  {e['error'][:88]}")
        if len(errors) > 4:
            print(f"     ... and {len(errors) - 4} more")
        print("  These are EXCLUDED from conformance — they are not evidence "
              "about generation.")
    if not n:
        print("\n  No question completed. There is no conformance figure to report.")
        return 1
    share = len(errors) / (len(errors) + n)
    if share > 0.2:
        print(f"\n  WARNING: {share:.0%} of attempts failed in transport. Treat the "
              f"figures below\n  as provisional and rerun on a stable connection.")
    ok = sum(r["answered"] for r in rows)
    gated = sum(1 for r in rows if any(c.startswith("gate:") for c in r.get("failures", [])))
    ret = sum(1 for r in rows if r.get("retried"))
    secs = sorted(r["seconds"] for r in rows if r.get("seconds"))
    right_ch = sum(1 for r in rows if r["answered"]
                   and r.get("cited_chapter") == r["expected_chapter"])

    print(f"\n  backend={args.backend}  n={n}  ({time.time() - t0:.0f}s total)")
    print(f"  contract conformance   {ok}/{n} = {ok / n:.0%}      (§12.1 bar: 100%)")
    print(f"  refused by the gate    {gated}/{n}")
    print(f"  failed the contract    {n - ok - gated}/{n}")
    print(f"  needed a retry         {ret}/{n}")
    if ok:
        print(f"  cited the right chapter {right_ch}/{ok} of answered")
    if secs:
        p = lambda q_: secs[min(int(q_ * len(secs)), len(secs) - 1)]  # noqa: E731
        print(f"  latency  median {p(0.5):.1f}s   p95 {p(0.95):.1f}s"
              f"      (§7.3 guardrail: 20s p95)")
    fc = Counter(c for r in rows for c in r.get("failures", []))
    if fc:
        print("\n  failure codes:")
        for code, cnt in fc.most_common():
            print(f"     {cnt:>3}  {code}")

    name = f"generation_{args.backend}{('_' + args.tag) if args.tag else ''}.json"
    out_path = ROOT / "eval" / name
    out_path.write_text(json.dumps({
        "backend": args.backend, "n": n, "conformance": ok / n,
        "transport_errors": len(errors),
        "transport_error_share": round(share, 4),
        "gate_refusals": gated, "contract_failures": n - ok - gated,
        "retries": ret, "chapter_precision": (right_ch / ok) if ok else None,
        "latency_median": secs[len(secs) // 2] if secs else None,
        "failure_codes": dict(fc), "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
