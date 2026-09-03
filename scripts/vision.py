"""Reading a textbook page image, for the questions the text layer cannot answer.

The refusal gate has two layers that exist purely because the extractor cannot
read pictures:

  answer_is_in_a_chart   the value the question asks for is plotted, not written
  figure_value_lookup    the question points at a figure ("नक्शे में … कहाँ है?")

Those refusals are honest but they are a coverage loss, and the information is
right there on a page we already have rendered at 300 dpi. A vision model can
read it.

WHAT IS SENT, AND WHY THAT MATTERS

Only a page of the published NCERT textbook, plus the parent's question. Not a
photograph of the child's homework, not the parent's voice. §14 objects to a
provider that trains on submitted data, and that objection is serious for a
child's work — it is close to meaningless for a page of a public textbook that
Google has almost certainly already crawled. If this is later extended to accept
a parent's photograph of their child's book, that is a different decision and
needs making deliberately.

Provider-agnostic on purpose, the same way voice is (D10): `read_figure` is the
contract, Gemini is one implementation, and swapping it out is a config change
rather than a rewrite.

GROUNDING

The prompt forbids the model from computing, inferring or adding anything — it
transcribes what is drawn. Its output is appended to the retrieved passage as
extra context for the normal generation path, so the answer contract, the
groundedness check and the four-part validator all still apply. A figure reading
is evidence, not an answer.

Credentials: GEMINI_API_KEY in .env. Absent, every call raises NotConfigured and
the gate refuses exactly as it did before.
"""

from __future__ import annotations

import base64
import io
import json
import os
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

MODEL = os.environ.get("SAATHI_VISION_MODEL", "gemini-2.5-flash")
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
PAGE_WIDTH = 900          # enough to read Devanagari body text; 128 KB at q72
PAGE_QUALITY = 72
TIMEOUT = 60

# Transcribe, do not solve. Every clause here is load-bearing: the whole value of
# a figure reading is that it is grounded in ink on the page, and a model that
# helpfully computes the answer destroys that.
PROMPT = """यह कक्षा 5 की गणित की पाठ्यपुस्तक (NCERT गणित मेला) का एक पृष्ठ है।

माता-पिता का सवाल: {question}

इस पृष्ठ पर जो चित्र, तालिका, आरेख या संख्याएँ छपी हैं, उनमें से केवल वही
बताइए जो इस सवाल से जुड़ी हैं।

नियम:
- केवल वही लिखिए जो पृष्ठ पर छपा है। अपनी तरफ़ से कुछ न जोड़िए।
- कोई गणना मत कीजिए। जोड़ना, घटाना, गुणा — कुछ नहीं।
- सवाल का जवाब मत दीजिए। सिर्फ़ बताइए कि पृष्ठ पर क्या लिखा/बना है।
- अगर इस पृष्ठ पर इस सवाल से जुड़ी कोई बात नहीं है, तो केवल यह लिखिए: NONE
- 60 शब्दों से कम।"""

# An ASCII sentinel, not a Devanagari one. The first version asked for
# "अपर्याप्त" (the marker the generator uses) and the model wrote "अपरिप्याप्त" —
# misspelled — so the check missed it and the module returned that as if it were
# figure content. An irrelevant page would then have fed noise into generation
# as evidence, which is the exact fabrication risk this module has to avoid.
# Asking a model to reproduce a Devanagari word exactly is a weak instrument;
# four ASCII letters are not. The Devanagari prefix is kept as a fallback for
# the misspelling that has already been observed.
NOTHING_THERE = "NONE"
_NOTHING_DEVA_PREFIX = "अपर"


class NotConfigured(RuntimeError):
    """No vision credentials. The chart refusals stand as they did before."""


class VisionError(RuntimeError):
    pass


def _load_env() -> None:
    """Read .env if the key is not already in the environment.

    Self-contained on purpose: serve.py starts its status checks in a thread
    before anything has loaded .env, so relying on the caller reported "figures
    not connected" on a machine where the key was sitting in the file.
    """
    if os.environ.get("GEMINI_API_KEY"):
        return
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, val = line.split("=", 1)
        os.environ.setdefault(k.strip(), val.strip())


def _key() -> str:
    _load_env()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise NotConfigured(
            "Set GEMINI_API_KEY in .env to let the system read textbook figures; "
            "without it, chart and figure questions are refused as before."
        )
    return key


def _page_jpeg(page: int) -> bytes:
    """The rendered page, downscaled. Raises if the page was never rendered."""
    src = ROOT / "ingest" / "pages" / f"p{page:03d}.png"
    if not src.exists():
        raise VisionError(f"page render missing: {src}")
    from PIL import Image

    img = Image.open(src).convert("RGB")
    if img.width > PAGE_WIDTH:
        h = round(img.height * PAGE_WIDTH / img.width)
        img = img.resize((PAGE_WIDTH, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=PAGE_QUALITY, optimize=True)
    return buf.getvalue()


def _post(body: dict, key: str) -> dict:
    """POST with the key in a header FILE, never on the command line.

    A credential in argv is visible to every process on the machine and lands in
    shell history; this project has already had two keys pasted into a chat and
    does not need a third exposure route.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        hdr = pathlib.Path(tmp) / "h"
        hdr.write_text(f"x-goog-api-key: {key}\n", encoding="utf-8")
        hdr.chmod(0o600)
        payload = pathlib.Path(tmp) / "b.json"
        payload.write_text(json.dumps(body), encoding="utf-8")
        proc = subprocess.run(
            ["curl", "-sS", "--max-time", str(TIMEOUT), "-H", f"@{hdr}",
             "-H", "Content-Type: application/json", "-X", "POST",
             f"{ENDPOINT}/{MODEL}:generateContent", "-d", f"@{payload}"],
            capture_output=True, text=True,
        )
    if proc.returncode != 0:
        raise VisionError(f"network: {proc.stderr.strip()[:200]}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        raise VisionError(f"unparseable: {proc.stdout[:200]!r}") from None


def read_figure(page: int, question: str) -> dict:
    """Transcribe the parts of a textbook page relevant to `question`.

    Returns {"text": str|None, "seconds": float, "tokens": int, "page": int}.
    `text` is None when the page holds nothing relevant — the caller must then
    keep the refusal rather than inventing coverage.
    """
    key = _key()
    jpeg = _page_jpeg(page)
    t0 = time.time()
    out = _post({
        "contents": [{"parts": [
            {"text": PROMPT.format(question=question)},
            {"inline_data": {"mime_type": "image/jpeg",
                             "data": base64.b64encode(jpeg).decode()}},
        ]}],
        # Low temperature: this is transcription, not composition.
        # thinkingBudget 0 matters. Gemini 2.5 counts reasoning tokens against
        # maxOutputTokens, so with thinking on, a 2048 cap was spent thinking and
        # the transcription came back truncated mid-word. There is nothing to
        # reason about here — the task is to read what is printed.
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 900,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }, key)

    if "error" in out:
        raise VisionError(str(out["error"].get("message"))[:200])
    try:
        parts = out["candidates"][0]["content"]["parts"]
        text = " ".join(p["text"] for p in parts if "text" in p).strip()
    except (KeyError, IndexError, TypeError):
        raise VisionError(f"no text in response: {str(out)[:200]}") from None

    usage = out.get("usageMetadata") or {}
    stripped = text.strip()
    nothing = (
        not stripped
        or NOTHING_THERE in stripped[:40].upper()
        or _NOTHING_DEVA_PREFIX in stripped[:20]
        # a bare marker with punctuation, and nothing else, is still a decline
        or len(stripped) < 12
    )
    return {
        "text": None if nothing else text,
        "seconds": round(time.time() - t0, 2),
        "tokens": usage.get("totalTokenCount"),
        "page": page,
        "kb_sent": len(jpeg) // 1024,
        "model": MODEL,
    }


def available() -> dict:
    """Whether figure reading can be offered, for the interface to branch on."""
    try:
        _key()
    except NotConfigured as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "model": MODEL}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--question", required=True)
    args = ap.parse_args()

    status = available()
    if not status["ok"]:
        raise SystemExit(status["reason"])
    r = read_figure(args.page, args.question)
    print(json.dumps(r, ensure_ascii=False, indent=2))
