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

from answer_contract import (  # noqa: E402
    JARGON,
    JARGON_ADVISORY,
    failures_as_instruction,
    validate,
)
from query_gate import (  # noqa: E402
    has_beyond_class5_word,
    is_value_seeking,
    pre_check,
)
from retrieval import LexicalIndex, expand_query  # noqa: E402

CHUNKS = ROOT / "ingest" / "chunks.json"
DECOYS = ROOT / "ingest" / "decoy_chunks.json"
INDEX = ROOT / "ingest" / "index_combined.npz"

EMBED_MODEL = "BAAI/bge-m3"
# Recalibrated on production retrieval (D1-FINAL). The old 0.415 floor was fitted
# to dense-only scoring; hybrid scoring shifts the whole distribution upward.
SIM_FLOOR = 0.545
TOP_K = 5

# A decoy must beat the best Class 5 chunk by this much to disqualify a question.
#
# On dense-only retrieval the usable margin was 0.01 and coverage capped at 73%,
# because higher-class books teach Class 5 topics at greater length and won on
# similarity ("सम और विषम संख्या में क्या फर्क है?" lost to a Class 7 chunk by
# 0.071). Once retrieval improved (D3: hybrid scoring + query expansion), Class 5
# chunks outscore the decoys and a much wider margin becomes safe:
#
#   margin   coverage   wrong   threshold
#   0.01       74%       1.3%     0.535
#   0.05       82%       1.2%     0.545
#   0.07       84%       1.2%     0.545   <- in use
#   0.08       85%       1.2%     0.545   <- §8.1's rule would pick this
#   0.09       74%       1.3%     0.670   <- one more leak forces the floor up
#   0.12       cannot reach the 2% budget at any threshold
#
# §8.1 step 4 says take the highest coverage inside the 2% budget, which is 0.08.
# 0.07 is used instead: the plateau from 0.05 to 0.08 has identical headroom
# (~0.8pp under budget), and the discontinuity at 0.09 is caused by a single
# question out of 150 — on a set that small, one item is ~1.2pp of wrong-answer
# rate, so standing one step back from the cliff costs 1 point of coverage and
# buys stability. Revisit once the set is built from real parent questions.
DECOY_MARGIN = 0.07

# How many Class 5 chunks are handed to the generator. §11.1 specifies "retrieve
# top-k ... generate against retrieved chunks only" — plural — and passing only
# the single best chunk was a real gap against that design: on a 30-question
# eval, 10 questions had the model correctly decline because the ONE chunk it
# received did not contain the answer, even though the right chapter was found.
# Chapter-level retrieval accuracy of 97% was hiding a chunk-level miss rate of
# about a third.
CONTEXT_CHUNKS = 3
CONTEXT_CHARS = 900  # per chunk, so three fit inside the token budget

# Weight of the lexical (IDF term-overlap) signal alongside dense cosine
# similarity. Dense retrieval alone missed chunks that contain the very word the
# parent asked about (see scripts/retrieval.py).
LEXICAL_WEIGHT = 0.35

class RateLimited(RuntimeError):
    """Per-minute limit; retrying after a pause will work."""


class RateLimitExhausted(RuntimeError):
    """Per-day limit; nothing will work until it resets. Raised so an eval stops
    and says so, instead of recording a run of failures as a quality score — a
    spent daily budget once produced a meaningless 20% conformance figure."""


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

# The jargon substitutions are generated FROM the validator's table, so the
# instruction the model gets and the rule it is judged by cannot drift apart.
_JARGON_LINE = "; ".join(
    f"'{k}' की जगह '{v}'"
    for k, v in list({**JARGON, **JARGON_ADVISORY}.items())[:10]
)

# Emitted verbatim by the model when the retrieved passage cannot answer the
# question. A decline is a SUCCESS, not a contract failure: the model correctly
# said "पाठ में समकोण की बात नहीं है।" and was scored as four missing parts. It
# now returns a clean refusal and logs a content gap, which is §8.1's ingest
# backlog rather than a generation defect.
DECLINE_MARKER = "अपर्याप्त"

SYSTEM_PROMPT = f"""आप एक शिक्षक-सहायक हैं। आपका काम बच्चे को पढ़ाना नहीं है — आपका काम \
माता-पिता को यह बताना है कि वे अपने बच्चे को कैसे समझाएँ।

सुनने वाला कौन है: एक माता या पिता जिनकी पढ़ाई लगभग कक्षा 8 तक हुई है, जिन्हें गणित के \
शब्द नहीं आते, और जिनके पास एक मिनट है।

उत्तर का ढाँचा — ठीक चार भाग, इसी क्रम में, हर भाग नई पंक्ति में "1." "2." "3." "4." से शुरू:

1. जवाब — सिर्फ एक वाक्य।
2. एक नियम — सिर्फ एक वाक्य, आसान शब्दों में।
3. उसी तरीके को नई संख्याओं पर हल कर के दिखाइए। इसमें संख्याएँ लिखना ज़रूरी है — \
सिर्फ बात कहना काफी नहीं। सवाल में जो संख्याएँ आई हैं, उन्हीं पर दोबारा हल मत कीजिए; \
तरीका वही रखिए पर संख्याएँ अपनी ओर से नई लीजिए।
4. एक वाक्य, उद्धरण चिह्नों "…" में, जो माता-पिता अपने बच्चे से सीधे कह सकें।

सख्त नियम:
- सिर्फ ऊपर दिए किताब के पाठ के आधार पर लिखिए। पाठ में जो नहीं है, वह मत जोड़िए — \
कोई नया नियम, कोई डिग्री, कोई सूत्र, कोई ऐसी संख्या नहीं जो अंश में न हो। \
भाग 1 और भाग 2 में केवल वही संख्याएँ आ सकती हैं जो पाठ में या सवाल में हैं।
- अगर पाठ में इस सवाल का जवाब नहीं है, तो चारों भाग मत लिखिए। केवल यह एक शब्द \
लिखिए और कुछ नहीं: {DECLINE_MARKER}
- पूरा उत्तर 100 शब्दों से कम। पूरी तरह देवनागरी हिंदी में, कोई अंग्रेज़ी शब्द नहीं।
- केवल एक तरीका बताइए। दूसरा विकल्प मत दीजिए।
- गणित के कठिन शब्द मत लिखिए। {_JARGON_LINE} लिखिए।

"""


USER_TEMPLATE = """किताब का पाठ (कक्षा 5 गणित — {title}):
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
        msg = data["error"].get("message", "")
        # Distinguish "wait a moment" from "come back tomorrow". Measured limits
        # on the free tier: 8,000 tokens per minute AND 200,000 tokens per day.
        # At ~2,200 tokens per answer with three-chunk context, the daily cap is
        # about 90 answers — the real §13.3 constraint, and tight enough that an
        # eval run competes with the pilot for the same budget.
        if "tokens per day" in msg or "TPD" in msg:
            raise RateLimitExhausted(f"groq daily token budget spent: {msg}")
        if "rate limit" in msg.lower():
            raise RateLimited(msg)
        raise RuntimeError(f"groq: {msg}")
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


_lex: LexicalIndex | None = None
_lex_ids: list[str] = []


def _lexical(ids: list[str], by_id: dict[str, dict], question: str) -> np.ndarray:
    global _lex, _lex_ids
    if _lex is None or _lex_ids != ids:
        _lex = LexicalIndex([
            f"{by_id[i].get('section_header_hi', '')} {by_id[i]['text_hi']}" for i in ids
        ])
        _lex_ids = list(ids)
    return np.asarray(_lex.score(question), dtype=np.float32)


def retrieve(question: str) -> tuple[list[tuple[dict, float]], dict]:
    """Top-k hits, plus the best score within each corpus so the gate can compare
    Class 5 against the decoys by margin rather than by rank.

    Scores are hybrid: dense cosine plus an IDF term-overlap term. Both corpora
    are scored the same way, so the decoy comparison stays like-for-like.
    """
    by_id = load_chunks()
    data = np.load(INDEX, allow_pickle=False)
    vectors, ids = data["vectors"], [str(x) for x in data["ids"]]
    # Expanded with the textbook's vocabulary for any colloquial term used, so a
    # parent asking about a "नक्शा" reaches a chapter that only ever says
    # "मानचित्र" (see retrieval.PARENT_TO_BOOK).
    expanded = expand_query(question)
    qv = _embed([expanded])[0]
    sims = vectors @ qv + LEXICAL_WEIGHT * _lexical(ids, by_id, expanded)
    order = np.argsort(-sims)[:TOP_K]
    hits = [(by_id[ids[i]], float(sims[i])) for i in order]

    best_c5 = best_c5_hit = None
    best_decoy = 0.0
    for i, cid in enumerate(ids):
        sc = float(sims[i])
        if by_id[cid]["in_syllabus"]:
            if best_c5 is None or sc > best_c5:
                best_c5, best_c5_hit = sc, by_id[cid]
        elif sc > best_decoy:
            best_decoy = sc
    # the best Class 5 chunks in score order, for the generator's context
    c5_scored = sorted(
        ((float(sims[i]), by_id[cid]) for i, cid in enumerate(ids)
         if by_id[cid]["in_syllabus"]),
        key=lambda t: -t[0],
    )[:CONTEXT_CHUNKS]
    corpora = {"best_class5": best_c5 or 0.0, "best_class5_chunk": best_c5_hit,
               "best_decoy": best_decoy,
               "decoy_lead": (best_decoy - (best_c5 or 0.0)),
               "class5_context": c5_scored}
    return hits, corpora


# ------------------------------------------------------------------- the gate
def gate(question: str, hits: list[tuple[dict, float]], corpora: dict) -> dict:
    """The calibrated layered gate (DECISIONS.md D1). Returns a refusal reason,
    or None to proceed."""
    pre = pre_check(question)
    if pre["outcome"] != "pass":
        return {"refuse": True, "reason": pre["reason"], "layer": "query_pre_check"}

    # Out of syllabus by the question's own vocabulary — categorical, no score
    # needed. Catches 17 of 20 higher-class maths questions with zero false
    # positives on the 100 legitimate ones.
    if has_beyond_class5_word(question):
        return {"refuse": True, "reason": "beyond_class5_topic_in_question",
                "layer": "query_vocabulary"}

    if corpora["decoy_lead"] >= DECOY_MARGIN:
        return {"refuse": True, "reason": "out_of_syllabus_by_margin",
                "layer": "class_metadata", "score": corpora["best_decoy"],
                "decoy_lead": round(corpora["decoy_lead"], 4)}

    # From here the Class 5 corpus is the one being answered from, even if a decoy
    # nominally outranked it inside the margin.
    top = corpora["best_class5_chunk"] or hits[0][0]
    score = corpora["best_class5"]
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
    hits, corpora = retrieve(question)
    decision = gate(question, hits, corpora)
    top = corpora["best_class5_chunk"] or hits[0][0]
    score = corpora["best_class5"]

    if decision["refuse"]:
        return {
            "answered": False,
            "refusal": {
                "spoken": REFUSAL_LINE,
                "page_image": f"ingest/pages/p{top['page']:03d}.png"
                if top.get("in_syllabus") else None,
                "reason": decision["reason"],
                "layer": decision["layer"],
            },
            "top_score": round(score, 4),
            "seconds": round(time.time() - t0, 2),
            # every refusal is logged as a content gap, which becomes the ingest
            # backlog (§8.1: refusals are a product input, not just a failure)
            "content_gap": {"question": question, "reason": decision["reason"]},
        }

    passages = corpora.get("class5_context") or [(score, top)]
    context = "\n\n---\n\n".join(
        f"[अध्याय {c['chapter']} — {c['chapter_title_hi']}, पेज {c['page']}]\n"
        f"{c['text_hi'][:CONTEXT_CHARS]}"
        for _sc, c in passages
    )
    user = USER_TEMPLATE.format(
        title=top["chapter_title_hi"], context=context, question=question,
    )
    generate = BACKENDS[backend]

    attempts = []
    raw, limits = generate(SYSTEM_PROMPT, user)

    if DECLINE_MARKER in raw[:80]:
        return {
            "answered": False,
            "refusal": {
                "spoken": REFUSAL_LINE,
                "page_image": f"ingest/pages/p{top['page']:03d}.png",
                "reason": "passage_does_not_cover_question",
                "layer": "generator_declined",
            },
            "citation": {"chapter": top["chapter"], "page": top["page"],
                         "chunk_id": top["id"]},
            "score": round(score, 4),
            "seconds": round(time.time() - t0, 2),
            "content_gap": {"question": question, "reason": "passage_gap",
                            "chunk_id": top["id"], "page": top["page"]},
        }

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
        "context_chunks": [
            {"chapter": c["chapter"], "page": c["page"], "score": round(sc, 4)}
            for sc, c in passages
        ],
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


def _shape(example: str) -> str:
    return f'1. ठीक है।\n2. यह एक नियम है।\n3. {example}\n4. "बेटा, ऐसे करो।"'


def selftest() -> int:
    cases = [
        ("well-formed answer", GOOD, "क्या 1/3, 2/6 के समान है?", True),
        ("part 3 reuses the question's numbers", BAD_REUSES_NUMBERS,
         "क्या 1/3, 2/6 के समान है?", False),
        ("jargon, and parts 3 and 4 missing", BAD_JARGON_AND_MISSING,
         "भाग कैसे देते हैं?", False),
        # the four shapes of the different-numbers rule (§10). Strict zero overlap
        # is unsatisfiable for the conceptual shape, mere novelty is too weak for
        # the transcription shape; these four pin the intended behaviour.
        ("conceptual question, fresh operand",
         _shape("जैसे 7 को 10 से गुणा करें तो 70 मिलता है, और 100 से गुणा करें तो 700 मिलता है।"),
         "10 और 100 से गुणा करने पर संख्या के साथ क्या होता है?", True),
        ("homework instance, fresh operands",
         _shape("जैसे 24 को 13 से गुणा करें तो 312 मिलता है।"),
         "35 को 12 से गुणा करना है, कैसे करें?", True),
        ("homework instance, solves the same sum",
         _shape("जैसे 35 को 12 से गुणा करें तो 420 मिलता है।"),
         "35 को 12 से गुणा करना है, कैसे करें?", False),
        ("example with no numbers at all",
         _shape("जैसे किसी संख्या को गुणा करके देख लीजिए।"),
         "35 को 12 से गुणा करना है, कैसे करें?", False),
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
