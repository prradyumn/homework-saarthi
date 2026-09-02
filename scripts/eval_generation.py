"""Measure answer-contract conformance for a generation backend (PRD §12.1).

Track A asserts contract conformance — all four parts present, and part 3's
different-numbers rule — alongside answer accuracy. Conformance is the half that
can be measured without a human, so it is measured here.

The number that matters for the tiering decision (DECISIONS.md D4): a backend
that cannot clear the contract does not produce wrong answers, it produces
refusals. That is safe but useless, and it is worth knowing which.
"""
import json, pathlib, sys, time
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from answer import answer  # noqa: E402

QUESTIONS = [
    "तुल्य भिन्न क्या होती है? 1/3 और 2/6 एक जैसी कैसे हैं?",
    "1 किलोग्राम में कितने ग्राम होते हैं?",
    "1 मिनट में कितने सेकंड होते हैं?",
    "समकोण क्या होता है? बेटी पूछ रही है",
    "सम पंचभुज से टाइल क्यों नहीं बन पाती?",
    "10 और 100 से गुणा करने पर संख्या के साथ क्या होता है?",
    "भाग देने का सूत्र क्या है, भाज्य भाजक वाला?",
    "किलोमीटर को मीटर में कैसे बदलते हैं?",
]

def main() -> int:
    backend = sys.argv[1] if len(sys.argv) > 1 else "groq"
    rows, t0 = [], time.time()
    # Free-tier limit measured off the live response headers: 8,000 tokens per
    # MINUTE (not requests per day, as the PRD assumed). At ~1,500 tokens per
    # call that is ~5 calls/min, so space them out rather than eat 429s.
    pace = 13.0 if backend == "groq" else 0.0
    for i, q in enumerate(QUESTIONS):
        if i and pace:
            time.sleep(pace)
        try:
            out = answer(q, backend=backend, verbose=False)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {q[:38]}: {exc}", flush=True)
            rows.append({"q": q, "answered": False, "error": str(exc)})
            continue
        ok = out.get("answered", False)
        codes = []
        if not ok and out.get("attempts"):
            codes = [f["code"] for f in out["attempts"][-1]["validation"]["failures"]]
        rows.append({"q": q, "answered": ok, "retried": out.get("retried"),
                     "seconds": out.get("seconds"), "failures": codes,
                     "words": (out.get("stats") or {}).get("words")})
        print(f"  {'OK ' if ok else 'REF'} {out.get('seconds', 0):>6.1f}s  "
              f"{q[:40]:42} {'' if ok else codes}", flush=True)

    n = len(rows)
    ok = sum(r["answered"] for r in rows)
    ret = sum(1 for r in rows if r.get("retried"))
    secs = [r["seconds"] for r in rows if r.get("seconds")]
    print(f"\n  backend={backend}")
    print(f"  contract conformance: {ok}/{n} = {ok/n:.0%}")
    print(f"  needed a retry: {ret}/{n}")
    if secs:
        secs.sort()
        print(f"  latency: median {secs[len(secs)//2]:.1f}s  p95 {secs[min(int(.95*len(secs)), len(secs)-1)]:.1f}s"
              f"   (§7.3 guardrail: 20s p95)")
    from collections import Counter
    fc = Counter(c for r in rows for c in r.get("failures", []))
    if fc:
        print("  failure codes:", dict(fc.most_common()))
    out = ROOT / "eval" / f"generation_{backend}.json"
    out.write_text(json.dumps({"backend": backend, "conformance": ok/n,
                               "total_seconds": round(time.time()-t0), "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {out}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
