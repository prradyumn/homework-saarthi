"""Hinglish in — Devanagari before the gate ever sees it.

Most Indian users type Roman script. A parent on a low-cost Android often has no
Devanagari keyboard at all, so "ye concept batao" is not an edge case, it is how
the question actually arrives. Before this module existed the pipeline handled
Devanagari (native) and English (D15) and silently failed on the script in
between.

**Measured, before any of this was written** — five Hinglish questions against
their exact Devanagari equivalents:

    0 of 5 retrieved the same chunk.
    3 of 5 were rejected by the gate as `no_maths_topic`, because the pre-check
      vocabulary is Devanagari and none of it matched.
    2 of 5 PASSED the gate and retrieved the wrong chapter
      ("sam panchbhuj se tile kyon nahi banti" -> ch11 p154, not ch7 p94).

The two that passed are the dangerous ones. A rejection is safe: the parent is
told to ask again. A pass on the wrong chapter is a confident answer from an
unrelated passage — precisely the failure §8.1 exists to prevent, arriving
through the front door.

## Why an LLM and not a transliteration library

Real Hinglish is phonetic and inconsistent: *kya / kyaa*, *hai / hain / he*,
*bhinn / bhin / bhinna*. Scheme-based transliterators (ITRANS, ISO 15919) expect
one canonical spelling and mangle the rest. A model reads it the way a person
does. It also costs no new dependency: the Groq client is already here, and the
slim deploy container ships numpy, pymupdf and pillow only.

## Cost

~120 tokens per normalisation against ~2,200 for a full answer — about 5%. It
only fires when the deterministic detector says the text is Hinglish, so
Devanagari and plain English pay nothing at all. Results are cached, because a
parent retyping the same question should not be billed twice.
"""

from __future__ import annotations

import re
import time

# Hindi function words that survive romanisation and essentially never appear in
# an English maths question. These are what separate "ye concept batao" from
# "explain this concept" — matching on content words would misfire on loanwords
# like "kilogram", which both languages share.
HINDI_MARKERS = {
    "ye", "yeh", "ya", "vo", "woh", "kya", "kyu", "kyun", "kyon", "kaise", "kaisa",
    "kitne", "kitna", "kitni", "kaun", "kahan", "kab", "hai", "hain", "he", "ho",
    "hota", "hoti", "hote", "tha", "thi", "the", "ka", "ki", "ke", "ko", "se",
    "me", "mein", "par", "aur", "bhi", "nahi", "nahin", "na", "batao", "bataiye",
    "samjhao", "samjha", "samjhaiye", "sikhao", "karo", "kare", "karna", "dijiye",
    "mera", "meri", "mere", "apna", "bachcha", "bachche", "beta", "beti",
    "matlab", "tarika", "sawal", "jawab", "padhai", "homework",
}

# Maths vocabulary a parent romanises. Not used for detection (too shared with
# English), but a strong hint to the normaliser about the domain.
MATHS_HINTS = {
    "bhinn", "ginti", "sankhya", "jod", "ghata", "guna", "bhag", "gunankhand",
    "naksha", "manchitra", "kon", "tribhuj", "chaturbhuj", "panchbhuj", "vrit",
    "aakriti", "aakar", "kshetrafal", "parimap", "lambai", "chaudai", "bhar",
    "dashamlav", "pratishat", "samay", "gunaj", "apvartak", "sam", "visham",
}

_DEVA = re.compile(r"[ऀ-ॿ]")
_WORD = re.compile(r"[a-z]+")

_cache: dict[str, dict] = {}


def script_mix(text: str) -> dict:
    """How much of this is Devanagari vs Latin."""
    deva = len(_DEVA.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    total = deva + latin
    return {"devanagari": deva, "latin": latin,
            "latin_share": (latin / total) if total else 0.0}


def looks_hinglish(text: str) -> bool:
    """Roman-script Hindi, as opposed to Devanagari or actual English.

    Deterministic on purpose: this decides whether to spend a model call, and a
    model deciding whether to call a model is a good way to spend the daily
    budget on nothing.
    """
    mix = script_mix(text)
    # Already Devanagari, or mostly so — nothing to do.
    if mix["latin_share"] < 0.6:
        return False
    words = set(_WORD.findall(text.lower()))
    if not words:
        return False
    hits = words & HINDI_MARKERS
    # Two markers, or one marker in a short question. "how many grams in a
    # kilogram" has none of these; "1 kilogram me kitne gram hote hain" has four.
    return len(hits) >= 2 or (len(hits) == 1 and len(words) <= 4)


SYSTEM = (
    "You convert romanised Hindi (Hinglish) into Devanagari. "
    "Rules: output ONLY the Devanagari version, nothing else — no quotes, no "
    "explanation, no English. Keep the meaning and the word order exactly. Keep "
    "digits as digits. If a word is an English loanword commonly written in "
    "Devanagari (concept, homework, kilogram), write it in Devanagari as an "
    "Indian speaker would. Do not answer the question; only transliterate it."
)


def to_devanagari(text: str, backend=None) -> dict:
    """Hinglish -> Devanagari. Returns the original unchanged on any failure.

    Failing open matters: a normaliser that raises would take down a question
    that the old pipeline would at least have refused safely.
    """
    key = text.strip().lower()
    if key in _cache:
        return dict(_cache[key], cached=True)

    out = {"original": text, "text": text, "changed": False,
           "provider": None, "seconds": 0.0, "cached": False}
    if not looks_hinglish(text):
        return out

    if backend is None:
        from answer import call_groq as backend

    t0 = time.time()
    try:
        raw, _meta = backend(SYSTEM, text.strip())
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
        return out

    deva = (raw or "").strip().strip('"').strip("'").split("\n")[0].strip()
    # Trust it only if it actually came back in the target script. A model that
    # answers the question instead of transliterating it, or echoes the Latin
    # input, must not be allowed to replace the query.
    if deva and script_mix(deva)["latin_share"] < 0.4:
        out.update(text=deva, changed=True)
    out.update(provider="groq", seconds=round(time.time() - t0, 2))
    _cache[key] = dict(out, cached=False)
    return out


if __name__ == "__main__":
    import sys

    probes = sys.argv[1:] or [
        "ye concept batao bhinn ka",
        "1 kilogram me kitne gram hote hain",
        "tulya bhinn kya hoti hai",
        "naksha me jagah kaise dhundhte hain",
        "sam panchbhuj se tile kyon nahi banti",
        "how many grams are in one kilogram?",      # English — must NOT fire
        "तुल्य भिन्न क्या होती है?",                    # Devanagari — must NOT fire
        "explain equivalent fractions to my child",  # English — must NOT fire
    ]
    for p in probes:
        flag = "HINGLISH" if looks_hinglish(p) else "        "
        r = to_devanagari(p) if looks_hinglish(p) else {"text": p, "seconds": 0}
        arrow = f" -> {r['text']}" if r.get("changed") else ""
        print(f"  {flag}  {p[:44]:46}{arrow}")
