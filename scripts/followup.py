"""Depth on demand — without making the default answer longer.

The obvious way to give a parent "more detail" is a longer answer. D16 measured
that and it is wrong: more context inflated length without improving grounding,
and it was **length control that took contract conformance 73% → 83%**.

There is a product argument too. §10's four parts exist so the parent can *say*
the answer. A longer answer is one they read silently and cannot reproduce —
which is the answer-copying this product refuses to do.

So depth arrives as a second question rather than a fatter first one. Three
follow-ups, each run against the **same already-retrieved passage**:

    another_example   the child did not get it — give a fresh worked example
    simpler           the rule did not land — same content, plainer words
    explain_more      the parent wants to understand it themselves

Nothing about grounding or refusal changes: the passage was already retrieved and
already cleared the gate, so these re-use it rather than re-searching. Each costs
a normal generation (~2,200 tokens) and only when a parent actually taps.

**Each kind has its own contract.** Enforcing the full four parts here would make
every follow-up repeat the original answer; enforcing nothing would drop the
safety net at exactly the moment the parent is most confused. So each validator
checks the narrow thing that kind must get right, and all three check that the
reply came back in Hindi at all.

One rule is inherited unchanged from §10 and matters most here: a worked example
must use **different numbers from the question** — and, for a *second* example,
different numbers from the first one too. An "another example" that repeats the
first is worse than no button.
"""

from __future__ import annotations

import re
import time

from answer_contract import MAX_WORDS, _norm, numbers_in

KINDS = ("another_example", "simpler", "explain_more")

# Shorter than a full answer: these are supplements, not replacements.
MAX_WORDS_FOLLOWUP = 70

_DEVA = re.compile(r"[ऀ-ॿ]")

SYSTEM = (
    "आप एक शिक्षक हैं जो माता-पिता की मदद करते हैं ताकि वे अपने बच्चे को समझा सकें। "
    "आप हमेशा सरल हिंदी में लिखते हैं। आप किताब के दिए हुए हिस्से से बाहर कुछ नहीं बताते। "
    "आप बच्चे को सीधे जवाब नहीं देते — आप माता-पिता को समझाने का तरीका देते हैं।"
)

PROMPTS = {
    "another_example": (
        "बच्चे को पहला उदाहरण समझ नहीं आया। इसी बात का एक और उदाहरण दीजिए, "
        "बिलकुल अलग संख्याओं के साथ।\n\n"
        "इस तरह लिखिए:\n"
        "उदाहरण: <एक छोटा उदाहरण, अलग संख्याओं से>\n"
        "बच्चे से कहिए: \"<एक वाक्य जो माता-पिता बच्चे से कह सकें>\"\n\n"
        "ज़रूरी: जो संख्याएँ सवाल में हैं और जो पहले उदाहरण में थीं, "
        "उनसे अलग संख्याएँ लीजिए।"
    ),
    "simpler": (
        "माता-पिता को यह बात समझ नहीं आई। इसे और आसान शब्दों में समझाइए — "
        "कोई कठिन शब्द नहीं, छोटे वाक्य।\n\n"
        "इस तरह लिखिए:\n"
        "आसान भाषा में: <दो-तीन छोटे वाक्य>\n"
        "बच्चे से कहिए: \"<एक वाक्य>\""
    ),
    "explain_more": (
        "माता-पिता खुद यह बात ठीक से समझना चाहते हैं। थोड़ा और विस्तार से समझाइए — "
        "यह ऐसा क्यों होता है।\n\n"
        "इस तरह लिखिए:\n"
        "क्यों: <तीन-चार छोटे वाक्य, सिर्फ़ किताब के हिस्से से>"
    ),
}

_BODY_RX = {
    "another_example": re.compile(r"उदाहरण\s*[:：]\s*(.+?)(?=बच्चे से कहिए|$)", re.S),
    "simpler": re.compile(r"आसान भाषा में\s*[:：]\s*(.+?)(?=बच्चे से कहिए|$)", re.S),
    "explain_more": re.compile(r"क्यों\s*[:：]\s*(.+)", re.S),
}
_SAY_RX = re.compile(r"बच्चे से कहिए\s*[:：]\s*(.+)", re.S)


def _deva_share(text: str) -> float:
    deva = len(_DEVA.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return deva / (deva + latin) if (deva + latin) else 0.0


def split(raw: str, kind: str) -> dict:
    body = _BODY_RX[kind].search(raw or "")
    say = _SAY_RX.search(raw or "")
    out = {"text": (body.group(1).strip() if body else (raw or "").strip())}
    if say:
        out["say_to_child"] = say.group(1).strip().strip("\"'“”")
    return out


def validate(parts: dict, kind: str, *, question: str,
             previous_example: str = "", answer_text: str = "") -> list[dict]:
    """The narrow contract for this kind. Returns failures, empty means ok."""
    fails: list[dict] = []
    text = parts.get("text", "")

    if not text:
        fails.append({"code": "empty"})
        return fails

    # Answering in English is the failure D2 caught on `gpt-oss-120b`, and it is
    # useless to a parent who asked in Hindi.
    if _deva_share(text) < 0.6:
        fails.append({"code": "not_hindi", "devanagari_share": round(_deva_share(text), 2)})

    words = len(_norm(text).split())
    if words > MAX_WORDS_FOLLOWUP:
        fails.append({"code": "too_long", "words": words, "limit": MAX_WORDS_FOLLOWUP})

    if kind in ("another_example", "simpler") and not parts.get("say_to_child"):
        # The parent still has to say something to the child. A follow-up that
        # drops that line has stopped being this product.
        fails.append({"code": "missing_say_to_child"})

    if kind == "another_example":
        new = set(numbers_in(text))
        q = set(numbers_in(question))
        prev = set(numbers_in(previous_example))
        if new & q:
            # §10's different-numbers rule, unchanged.
            fails.append({"code": "reuses_question_numbers", "shared": sorted(new & q)})
        if new and new <= prev:
            # "Another" example that reuses the first one's numbers is not another
            # example — it is the same example, and worse than no button.
            fails.append({"code": "repeats_previous_example", "numbers": sorted(new)})
        if not new:
            fails.append({"code": "no_numbers_in_example"})

    if kind == "simpler":
        # A restatement must not be longer than what it restates.
        if answer_text and words > len(_norm(answer_text).split()):
            fails.append({"code": "not_simpler", "words": words})

    return fails


# Canned follow-ups, for the browser suite. The same lesson as `call_stub`: UI
# testing was once eating the budget the measurement needed, and a follow-up is a
# full generation. Retrieval, the passage lookup, validation and every line of
# rendering still run for real — only the network call is faked.
STUB = {
    "another_example": "उदाहरण: 1/5 के ऊपर और नीचे को 3 से गुणा कीजिए, तो 3/15 बनता है।\n"
                       "बच्चे से कहिए: \"देखो, 1/5 और 3/15 एक ही हैं।\"",
    "simpler": "आसान भाषा में: दो भिन्न अलग दिखते हैं पर बराबर होते हैं।\n"
               "बच्चे से कहिए: \"दोनों का हिस्सा एक जैसा है।\"",
    "explain_more": "क्यों: किताब में दिखाया गया है कि एक ही हिस्से को अलग-अलग "
                    "टुकड़ों में बाँटा जा सकता है।",
}


def ask(kind: str, *, question: str, passage: str, answer_text: str = "",
        previous_example: str = "", backend=None) -> dict:
    """Run one follow-up against the passage that was already retrieved.

    No re-retrieval and no re-gating: this passage already cleared §8.1, and
    searching again on a vaguer prompt ("explain more") would only risk drifting
    to a worse chunk than the one the gate approved.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown follow-up {kind!r}; choose from {KINDS}")
    if backend == "stub":
        raw = STUB[kind]
        parts = split(raw, kind)
        return {"ok": not validate(parts, kind, question=question,
                                   previous_example=previous_example,
                                   answer_text=answer_text),
                "kind": kind, "parts": parts, "failures": [], "raw": raw,
                "seconds": 0.0, "tokens": 0, "backend": "stub"}
    if backend is None:
        from answer import call_groq as backend

    user = (
        f"किताब का हिस्सा:\n{passage}\n\n"
        f"माता-पिता का सवाल: {question}\n"
        + (f"\nपहले दिया गया उदाहरण: {previous_example}\n" if previous_example else "")
        + f"\n{PROMPTS[kind]}"
    )

    t0 = time.time()
    try:
        raw, meta = backend(SYSTEM, user)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "kind": kind, "error": str(exc)[:220],
                "seconds": round(time.time() - t0, 2)}

    parts = split(raw, kind)
    fails = validate(parts, kind, question=question,
                     previous_example=previous_example, answer_text=answer_text)
    return {
        "ok": not fails,
        "kind": kind,
        "parts": parts if not fails else {},
        "failures": fails,
        "raw": raw,
        "seconds": round(time.time() - t0, 2),
        "tokens": (meta or {}).get("usage_total_tokens"),
    }


if __name__ == "__main__":
    import argparse
    import json
    import sys

    sys.path.insert(0, "scripts")
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=KINDS)
    ap.add_argument("--question", default="तुल्य भिन्न क्या होती है?")
    args = ap.parse_args()

    from answer import load_chunks, retrieve

    hits, _ = retrieve(args.question)
    top = hits[0][0]
    out = ask(args.kind, question=args.question, passage=top["text_hi"][:900])
    print(json.dumps(out, ensure_ascii=False, indent=2)[:1400])
