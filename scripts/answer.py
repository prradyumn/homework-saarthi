"""End-to-end answer path: gate → retrieve → generate → validate → answer or refuse.

The generator sits behind an interface so the tiering from DECISIONS.md is real
rather than aspirational:

  1  Groq free tier            primary; set GROQ_API_KEY
  2  Ollama, small local model offline / sovereignty demo (--backend ollama)
  3  refuse                    whenever the contract validator rejects the output

Tier 3 is what makes tier 2 safe. A weak local model does not produce a wrong
answer, it produces output that fails validation — and a failed validation is an
honest refusal with the textbook page attached, which §8.1 already designs for.
So the safety property is architectural, not model-dependent.

Nothing here is asserted: run `python scripts/answer.py --selftest` to exercise
the validator against handwritten good and bad answers, or
`python scripts/answer.py --question "…"` for the live path.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _load_env() -> None:
    """Read .env if present, without adding a dependency. .env is gitignored;
    the key never belongs in the repo or in a command line."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


_load_env()

from answer_contract import failures_as_instruction, validate  # noqa: E402
from query_gate import is_value_seeking, pre_check  # noqa: E402

CHUNKS = ROOT / "ingest" / "chunks.json"
DECOYS = ROOT / "ingest" / "decoy_chunks.json"
INDEX = ROOT / "ingest" / "index_combined.npz"

EMBED_MODEL = "BAAI/bge-m3"
SIM_FLOOR = 0.415  # the calibrated operating point (DECISIONS.md D1)
TOP_K = 5

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq's catalogue moves; llama-3.3-70b-versatile was retired between the PRD
# being written and this being built. Query /v1/models rather than trusting a
# remembered name, and pick by measured Hindi contract conformance
# (scripts/eval_generation.py), not by parameter count.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3:1.7b"

# The refusal package (§8.1): never a bare no. A spoken line, the textbook page,
# and a route to a human.
REFUSAL_LINE = (
    "मुझे पक्का नहीं पता, और गलत बताकर आपका नुकसान नहीं करना चाहता। "
    "किताब का यह पेज देख लीजिए, और कल शिक्षक जी से एक बार पूछ लीजिए।"
)

SYSTEM_PROMPT = """आप एक शिक्षक-सहायक हैं। आपका काम बच्चे को पढ़ाना नहीं है — आपका काम \
माता-पिता को यह बताना है कि वे अपने बच्चे को कैसे समझाएँ।

सुनने वाला कौन है: एक माता या पिता जिनकी पढ़ाई लगभग कक्षा 8 तक हुई है, जिन्हें गणित के \
शब्द नहीं आते, और जिनके पास एक मिनट है।

नियम, जिनका पालन अनिवार्य है:
- उत्तर ठीक चार भागों में दीजिए, इसी क्रम में, हर भाग नई पंक्ति में, "1." "2." "3." "4." से शुरू।
- भाग 1: उत्तर, सिर्फ एक वाक्य।
- भाग 2: वह एक नियम जो समझ में आ जाए, सिर्फ एक वाक्य, कोई कठिन शब्द नहीं।
- भाग 3: वही तरीका, लेकिन अलग संख्याओं के साथ हल कर के दिखाइए। सवाल की संख्याएँ \
दोबारा मत लिखिए।
- भाग 4: एक वाक्य, उद्धरण चिह्नों में, जो माता-पिता अपने बच्चे से सीधे कह सकें।
- पूरा उत्तर 100 शब्दों से कम। पूरी तरह देवनागरी हिंदी में।
- केवल एक तरीका बताइए। दूसरा विकल्प मत दीजिए।
- गणित के कठिन शब्द (अंश, हर, भाज्य, भाजक, क्षेत्रफल) मत लिखिए। आसान शब्द लिखिए, \
जैसे "ऊपर वाला अंक", "नीचे वाला अंक", "कितनी जगह घेरता है"।
- जो किताब के अंश में नहीं है, वह मत जोड़िए।"""

USER_TEMPLATE = """किताब का अंश (कक्षा 5 गणित, अध्याय {chapter} — {title}, पेज {page}):
\"\"\"
{context}
\"\"\"

माता-पिता का सवाल: {question}

अब चार भागों में उत्तर दीजिए।"""


# ------------------------------------------------------------------ generation
def call_groq(system: str, user: str, timeout: int = 60) -> tuple[str, dict]:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set — get one at console.groq.com/keys")
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": 700,
    }
    # Headers go to their own file so stdout stays pure JSON. Splitting a
    # combined stream on a blank line silently produced an empty body and turned
    # a clear API error ("model does not exist") into a KeyError.
    with tempfile.NamedTemporaryFile("w+", suffix=".hdr", delete=False) as hf:
        hdr_path = hf.name
    try:
        proc = subprocess.run(
            ["curl", "-sS", "-D", hdr_path, "--max-time", str(timeout), GROQ_URL,
             "-H", f"Authorization: Bearer {key}",
             "-H", "Content-Type: application/json", "-d", "@-"],
            input=json.dumps(payload), capture_output=True, text=True,
        )
        head = pathlib.Path(hdr_path).read_text(errors="replace")
    finally:
        pathlib.Path(hdr_path).unlink(missing_ok=True)

    # Read the real rate limits off the response rather than trusting a
    # remembered figure — free-tier limits move.
    limits = {
        k.strip().lower().replace("x-ratelimit-", ""): v.strip()
        for line in head.splitlines() if ":" in line
        for k, v in [line.split(":", 1)]
        if k.strip().lower().startswith("x-ratelimit")
    }
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        raise RuntimeError(f"groq: unparseable response: {proc.stdout[:300]!r}") from None
    if "error" in data:
        raise RuntimeError(f"groq: {data['error'].get('message')}")
    if "choices" not in data:
        raise RuntimeError(f"groq: no choices in response: {str(data)[:300]}")
    return data["choices"][0]["message"]["content"], limits


def call_ollama(system: str, user: str, timeout: int = 300) -> tuple[str, dict]:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"{system}\n\n{user}",
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_predict": 700},
    }
    proc = subprocess.run(
        ["curl", "-sS", "--max-time", str(timeout), OLLAMA_URL, "-d", "@-"],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    data = json.loads(proc.stdout or "{}")
    if "error" in data:
        raise RuntimeError(f"ollama: {data['error']}")
    return data.get("response", ""), {"backend": OLLAMA_MODEL}


BACKENDS = {"groq": call_groq, "ollama": call_ollama}


# ------------------------------------------------------------------- retrieval
_model = None


def _embed(texts: list[str]) -> np.ndarray:
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMBED_MODEL, device="cpu")
    return _model.encode(texts, batch_size=4, normalize_embeddings=True,
                         convert_to_numpy=True)


def load_chunks() -> dict[str, dict]:
    main = json.loads(CHUNKS.read_text(encoding="utf-8"))
    for c in main:
        c["in_syllabus"] = True
    decoys = json.loads(DECOYS.read_text(encoding="utf-8")) if DECOYS.exists() else []
    return {c["id"]: c for c in main + decoys}


def retrieve(question: str) -> list[tuple[dict, float]]:
    by_id = load_chunks()
    data = np.load(INDEX, allow_pickle=False)
    vectors, ids = data["vectors"], [str(x) for x in data["ids"]]
    qv = _embed([question])[0]
    sims = vectors @ qv
    order = np.argsort(-sims)[:TOP_K]
    return [(by_id[ids[i]], float(sims[i])) for i in order]


# ------------------------------------------------------------------- the gate
def gate(question: str, hits: list[tuple[dict, float]]) -> dict:
    """The calibrated layered gate (DECISIONS.md D1). Returns a refusal reason,
    or None to proceed."""
    pre = pre_check(question)
    if pre["outcome"] != "pass":
        return {"refuse": True, "reason": pre["reason"], "layer": "query_pre_check"}

    top, score = hits[0]
    if not top["in_syllabus"]:
        return {"refuse": True, "reason": f"out_of_syllabus_class_{top['class']}",
                "layer": "class_metadata", "score": score}
    if top.get("needs_review"):
        return {"refuse": True, "reason": "chunk_below_extraction_gate",
                "layer": "chunk_flag", "score": score}
    if is_value_seeking(question) and top.get("has_chart_axis"):
        return {"refuse": True, "reason": "answer_is_in_a_chart",
                "layer": "chart_axis", "score": score}
    if score < SIM_FLOOR:
        return {"refuse": True, "reason": "below_similarity_floor",
                "layer": "similarity", "score": score}
    return {"refuse": False, "score": score}


def answer(question: str, backend: str = "groq", verbose: bool = True) -> dict:
    t0 = time.time()
    hits = retrieve(question)
    decision = gate(question, hits)
    top, score = hits[0]

    if decision["refuse"]:
        return {
            "answered": False,
            "refusal": {
                "spoken": REFUSAL_LINE,
                "page_image": f"ingest/pages/p{top['page']:03d}.png" if top["in_syllabus"] else None,
                "reason": decision["reason"],
                "layer": decision["layer"],
            },
            "top_score": round(score, 4),
            "seconds": round(time.time() - t0, 2),
            # every refusal is logged as a content gap, which becomes the ingest
            # backlog (§8.1: refusals are a product input, not just a failure)
            "content_gap": {"question": question, "reason": decision["reason"]},
        }

    user = USER_TEMPLATE.format(
        chapter=top["chapter"], title=top["chapter_title_hi"], page=top["page"],
        context=top["text_hi"][:1400], question=question,
    )
    generate = BACKENDS[backend]

    attempts = []
    context = top["text_hi"][:1400]
    raw, limits = generate(SYSTEM_PROMPT, user)
    result = validate(raw, question, context=context)
    attempts.append({"raw": raw, "validation": result})

    if not result["ok"]:
        if verbose:
            print(f"  [retry] contract failures: "
                  f"{[f['code'] for f in result['failures']]}", file=sys.stderr)
        fix = failures_as_instruction(result["failures"])
        raw2, limits = generate(SYSTEM_PROMPT, f"{user}\n\nपिछले उत्तर में ये गलतियाँ थीं:\n{fix}")
        result = validate(raw2, question, context=context)
        attempts.append({"raw": raw2, "validation": result})

    if not result["ok"]:
        return {
            "answered": False,
            "refusal": {
                "spoken": REFUSAL_LINE,
                "page_image": f"ingest/pages/p{top['page']:03d}.png",
                "reason": "answer_failed_contract",
                "layer": "contract_validator",
                "failures": result["failures"],
            },
            "attempts": attempts,
            "seconds": round(time.time() - t0, 2),
            "content_gap": {"question": question, "reason": "contract_failure"},
        }

    return {
        "answered": True,
        "parts": result["parts"],
        "citation": {"chapter": top["chapter"], "title": top["chapter_title_hi"],
                     "page": top["page"], "chunk_id": top["id"]},
        "page_image": f"ingest/pages/p{top['page']:03d}.png",
        "score": round(score, 4),
        "stats": result["stats"],
        "rate_limits": limits,
        "retried": len(attempts) > 1,
        "seconds": round(time.time() - t0, 2),
    }


# -------------------------------------------------------------------- selftest
GOOD = """1. एक तिहाई और दो छठवाँ बराबर हैं।
2. अगर ऊपर और नीचे वाले दोनों अंकों को उसी संख्या से गुणा करें, तो भिन्न वही रहती है।
3. जैसे एक चौथाई को लीजिए — ऊपर और नीचे दोनों को 3 से गुणा कीजिए, तो तीन बारहवाँ मिलता है, और वह एक चौथाई के बराबर ही है।
4. "देखो बेटा, रोटी के टुकड़े छोटे कर दें तो टुकड़े ज़्यादा हो जाते हैं, पर रोटी उतनी ही रहती है।\""""

BAD_REUSES_NUMBERS = """1. 1/3 और 2/6 बराबर हैं।
2. ऊपर नीचे एक ही संख्या से गुणा कीजिए।
3. जैसे 1/3 को 2 से गुणा करें तो 2/6 मिलता है।
4. "दोनों बराबर हैं।\""""

BAD_JARGON_AND_MISSING = """1. अंश और हर को गुणा कीजिए।
2. भाज्य को भाजक से बाँटिए और भागफल देखिए।"""


def selftest() -> int:
    cases = [
        ("well-formed answer", GOOD, "क्या 1/3, 2/6 के समान है?", True),
        ("part 3 reuses the question's numbers", BAD_REUSES_NUMBERS,
         "क्या 1/3, 2/6 के समान है?", False),
        ("jargon, and parts 3 and 4 missing", BAD_JARGON_AND_MISSING,
         "भाग कैसे देते हैं?", False),
    ]
    ok = True
    for name, text, q, expect in cases:
        r = validate(text, q)
        mark = "PASS" if r["ok"] == expect else "UNEXPECTED"
        if r["ok"] != expect:
            ok = False
        print(f"  [{mark}] {name}")
        print(f"          valid={r['ok']}  words={r['stats']['words']}  "
              f"parts={sorted(r['parts'])}")
        if r["failures"]:
            for f in r["failures"]:
                print(f"          - {f}")
        print(f"          q_nums={r['stats']['question_numbers']} "
              f"ex_nums={r['stats']['example_numbers']}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--question")
    ap.add_argument("--backend", default="groq", choices=sorted(BACKENDS))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.question:
        ap.error("give --question or --selftest")

    out = answer(args.question, backend=args.backend)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("answered") else 2


if __name__ == "__main__":
    sys.exit(main())
