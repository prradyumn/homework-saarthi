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
import re
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
# The similarity floor is now INERT, and that is the finding rather than an
# oversight. Once the query-side pre-checks caught the low-scoring leaks
# categorically — a general-knowledge question at 0.414, an off-topic one at
# 0.30 — the sweep selected the bottom of its own range, meaning no floor was
# needed. It is kept low as defensive depth; the real backstop is the generator,
# which declines when its passages do not contain the answer.
#
# This is the third time strengthening a categorical layer has made the tuned
# score matter less (D1, D1-FINAL, and here).
SIM_FLOOR = 0.30
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
#   0.07       84%       1.2%     0.300
#   0.08       85%       1.2%     0.300   <- in use
#   0.09       85%       1.2%     0.300
#   0.10       86%       1.1%     0.300   <- §8.1's rule would pick this
#   0.12       cannot reach the 2% budget at any threshold
#
# Strengthening the query gate (the भारत/भार suffix-guard fix) widened the usable
# plateau from 0.05-0.08 to 0.05-0.10 and moved the cliff from 0.09 to 0.12.
#
# §8.1 step 4 says take the highest coverage inside budget, which is 0.10 at 86%.
# 0.08 is used instead, for the same reason as before: 0.08 and 0.09 both reach
# 85%, so three steps back from the cliff costs one point of coverage rather than
# two, and the cliff's position is still set by a single question out of 150.
DECOY_MARGIN = 0.08

# How many Class 5 chunks are handed to the generator. §11.1 specifies "retrieve
# top-k ... generate against retrieved chunks only" — plural — and passing only
# the single best chunk was a real gap against that design: on a 30-question
# eval, 10 questions had the model correctly decline because the ONE chunk it
# received did not contain the answer, even though the right chapter was found.
# Chapter-level retrieval accuracy of 97% was hiding a chunk-level miss rate of
# about a third.
# Tunable from the environment so the window experiment is a flag rather than an
# edit to a constant. 49% of in-syllabus chunks are longer than 900 chars (median
# 1340, p90 1437, max 1548), so this cap withholds 20% of the book's prose from
# the generator — see eval/coverage_diagnosis.json. Whether that costs conformance
# is measured by eval_generation.py, not assumed.
CONTEXT_CHUNKS = int(os.environ.get("SAATHI_CONTEXT_CHUNKS", "3"))
CONTEXT_CHARS = int(os.environ.get("SAATHI_CONTEXT_CHARS", "900"))

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

# A separate line for questions that are outside the book altogether. The first
# line promises a page; promising a page and then showing an unrelated one is
# worse than saying plainly that this is not in the Class 5 book.
REFUSAL_LINE_OFF_TOPIC = (
    "यह सवाल कक्षा 5 की गणित की किताब में नहीं है, इसलिए मैं इसका जवाब नहीं दे सकता। "
    "इससे मिलता-जुलता कोई सवाल किताब से पूछिए, या शिक्षक जी से पूछ लीजिए।"
)

# REFUSAL_LINE_OFF_TOPIC was being sent for EVERY query_pre_check code — but that
# layer emits six codes and only one of them means "not in the Class 5 book".
# Four mean "I could not understand the question" and two mean "I understood it
# and am declining on purpose", so the line asserted a cause that was untrue in
# six cases out of seven. §8.1 requires a refusal to be actionable; a wrong
# diagnosis is not actionable, it just misdirects the parent.
#
# Four of these are pre_check's `clarify` outcome, not `refuse`. They are requests
# to ask again, and the interface styles them accordingly rather than as warnings.
REFUSAL_LINE_UNCLEAR = (
    "मैं आपका सवाल ठीक से समझ नहीं पाया। किताब के जिस विषय की बात है वह बताकर फिर "
    "पूछिए — जैसे 'भिन्न', 'भाग', या 'क्षेत्रफल'।"
)
PRE_CHECK_LINES = {
    "too_short": REFUSAL_LINE_UNCLEAR,
    "no_maths_topic": REFUSAL_LINE_UNCLEAR,
    "two_questions": (
        "इसमें दो सवाल एक साथ हैं। एक बार में एक सवाल पूछिए — तो मैं हर एक को ठीक से "
        "समझा पाऊँगा।"
    ),
    # The code name says ASR, but the trigger is code-mixing, which a TYPED
    # question has too — "x2+5x+6 का हल क्या है?" lands here. Telling a parent who
    # typed to say it again is nonsense, and this layer does not know the input
    # mode, so the line must work for both. (The refusal itself is correct for
    # that example; only the label is off. Tightening the classifier is a gate
    # change that has to be measured against eval/refusal_set.py, not guessed at.)
    "asr_suspect_code_mixed": (
        "आपका सवाल मुझे पूरी तरह समझ नहीं आया। इसे हिंदी में फिर से पूछिए — लिखकर या "
        "बोलकर, जैसा आपको ठीक लगे।"
    ),
    # §2's positioning, stated to the parent rather than kept in the PRD: this is
    # a coach for parents, not an answer service for children.
    "answer_copying": (
        "मैं तैयार जवाब लिखकर नहीं देता — उससे बच्चा सीखता नहीं। यह पूछिए कि बच्चे को "
        "यह कैसे समझाएँ, और मैं समझाने का तरीका बता दूँगा।"
    ),
    "beyond_class5_topic_in_question": (
        "यह सवाल कक्षा 5 की किताब से आगे का है — यह गणित तो है, पर इस किताब में "
        "नहीं सिखाया गया। बड़ी कक्षा में यह आएगा, या शिक्षक जी से पूछ लीजिए।"
    ),
    "figure_value_lookup": (
        "इसका जवाब किताब के चित्र में है, जो मैं पढ़ नहीं सकता। किताब में वह चित्र देख "
        "लीजिए, या शिक्षक जी से पूछ लीजिए।"
    ),
}
# Codes whose line above already names the cause, so the interface must not add a
# second explanation underneath and say the same thing twice.
CAUSE_EXPLAINED = set(PRE_CHECK_LINES)
# pre_check's `clarify` outcome — an invitation to ask again, not a refusal.
CLARIFY_CODES = {"too_short", "no_maths_topic", "two_questions", "asr_suspect_code_mixed"}

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
    # Capture the provider's own token accounting. Without it the cost model had
    # to infer volumes three different ways — the limiter's per-request figure
    # (2,066-2,379), a local Qwen2.5 tokenizer on real retrievals (median 3,814),
    # and implied consumption from exhausting the daily budget (~2,900) — which
    # disagreed by up to 1.6x. `usage` settles it exactly, and it is free.
    limits.update({f"usage_{k}": v for k, v in (data.get("usage") or {}).items()
                   if isinstance(v, (int, float))})
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


def call_stub(system: str, user: str) -> tuple[str, dict]:
    """A four-part answer built from the retrieved passage, spending no tokens.

    The browser suite was calling the real model on every run. Four runs plus a
    few manual checks consumed 197,907 of the free tier's 200,000 tokens per day,
    which then aborted the conformance run at 1 of 30 questions. UI testing was
    eating the budget the measurement needed.

    This fakes ONLY the network call. Retrieval, the whole refusal gate and the
    contract validator still run for real — they are local, free, and most of what
    the interface renders. What is lost is exactly what costs money: whether the
    model writes a conforming answer. That is eval_generation.py's job, and it
    must never use this backend.

    The numbers are lifted out of the passage rather than invented, because the
    validator checks groundedness against the passage and rejects an example that
    only repeats the question's own numbers. A canned answer with made-up digits
    fails on `ungrounded_number` and renders as a refusal, which would make the
    interface tests assert the wrong thing.

    Selected by `--backend stub`, never by default; /api/status and the
    HOW IT WORKS panel both report it, so stub text cannot pass for a real answer.
    """
    passage, _, tail = user.partition("माता-पिता का सवाल:")
    q_nums = set(re.findall(r"\d+", tail))
    # small integers only: a page number or a four-digit year makes a poor example
    fresh = [n for n in re.findall(r"\d+", passage)
             if n not in q_nums and 0 < len(n) <= 2 and n != "0"]
    a1, a2 = (fresh + ["2", "4"])[:2]
    raw = (
        "1. इसका जवाब किताब के इसी पाठ में दिया गया है, जो ऊपर दिए अंश में है।\n"
        "2. हर बार इकाई से शुरू करके एक-एक कदम आगे बढ़ते हैं।\n"
        f"3. जैसे {a1} और {a2} जैसी संख्याओं के साथ यही तरीका आज़माकर देखिए।\n"
        "4. \"बेटा, इसे इकाई से शुरू करके एक-एक कदम करके देखो।\""
    )
    return raw, {"stub": True, "prompt_chars": len(system) + len(user)}


BACKENDS = {"groq": call_groq, "ollama": call_ollama, "stub": call_stub}


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
    """Chunks keyed by id, Class 5 first then the decoy corpus.

    The keying is asserted, not assumed. chunk.py once issued the same id to
    every chunk that began on a given page, and this dict comprehension dropped
    74 of 248 Class 5 chunks with no error at all — 30% of the book was invisible
    to retrieval, and every figure measured against "174 in-syllabus chunks" was
    measured against a truncated corpus.
    """
    main = json.loads(CHUNKS.read_text(encoding="utf-8"))
    for c in main:
        c["in_syllabus"] = True
    decoys = json.loads(DECOYS.read_text(encoding="utf-8")) if DECOYS.exists() else []
    rows = main + decoys
    by_id = {c["id"]: c for c in rows}
    if len(by_id) != len(rows):
        import collections
        dupes = {i: n for i, n in
                 collections.Counter(c["id"] for c in rows).items() if n > 1}
        raise RuntimeError(
            f"chunk id collision: {len(rows)} rows -> {len(by_id)} keys. "
            f"{len(rows) - len(by_id)} chunks would be silently dropped. {dupes}"
        )
    return by_id


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
    # Every Class 5 chunk in score order. The context is the head of this list;
    # the tail is kept because a diagnostic that ranks only within TOP_K *hits*
    # cannot see a covering chunk that lost its top-K slot to a decoy, and will
    # report a chunk as "not retrieved" when it was merely not displayed.
    c5_ranked = sorted(
        ((float(sims[i]), by_id[cid]) for i, cid in enumerate(ids)
         if by_id[cid]["in_syllabus"]),
        key=lambda t: -t[0],
    )
    c5_scored = c5_ranked[:CONTEXT_CHUNKS]
    corpora = {"best_class5": best_c5 or 0.0, "best_class5_chunk": best_c5_hit,
               "best_decoy": best_decoy,
               "decoy_lead": (best_decoy - (best_c5 or 0.0)),
               "class5_context": c5_scored, "class5_ranked": c5_ranked}
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
    # This refusal exists because the answer sits in a chart the extractor cannot
    # read. It used to test only the TOP chunk, so a 0.001 ranking difference
    # decided it: "1 किलोग्राम में कितने ग्राम होते हैं?" was refused because a
    # chart chunk scored 0.983 against 0.982 for the passage literally titled
    # "विभिन्न इकाइयाँ परंतु एक ही माप", which was sitting in the context anyway.
    # Refuse only when EVERY passage the generator receives is chart-derived —
    # otherwise let it answer from the prose, with the generator's own decline
    # marker as the backstop if it cannot.
    ctx = corpora.get("class5_context") or []
    if (is_value_seeking(question) and top.get("has_chart_axis")
            and all(c.get("has_chart_axis") for _s, c in ctx)):
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
        # §8.1 says a refusal ships "the textbook page image for the relevant
        # chapter". For an out-of-syllabus question there IS no relevant chapter,
        # and offering one anyway is actively confusing: a quadratic-equation
        # question was being answered with page 105, on kilograms and grams.
        # The page is offered only when the question is in-syllabus and we are
        # merely unsure — which is exactly when the page helps.
        no_relevant_page = decision["layer"] in (
            "query_vocabulary", "class_metadata", "query_pre_check",
        )
        return {
            "answered": False,
            "refusal": {
                "spoken": PRE_CHECK_LINES.get(
                    decision["reason"],
                    REFUSAL_LINE if not no_relevant_page else REFUSAL_LINE_OFF_TOPIC),
                "cause_explained": decision["reason"] in CAUSE_EXPLAINED,
                "kind": "clarify" if decision["reason"] in CLARIFY_CODES else "refusal",
                "page_image": None if no_relevant_page or not top.get("in_syllabus")
                else f"ingest/pages/p{top['page']:03d}.png",
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
