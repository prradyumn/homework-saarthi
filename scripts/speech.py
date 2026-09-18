"""Speech in and speech out, from whichever provider this box actually has.

Bhashini (§8.4) remains the *preferred* production path on sovereignty grounds,
but its API access is an approval process with no schedule attached, and a
product cannot wait on one. This picks a working provider and says honestly which
one it used.

    ASR   groq     whisper-large-v3 on Groq's free tier.  DEFAULT.
          bhashini ULCA, when credentials exist.
    TTS   bhashini ULCA, when credentials exist.
          browser  the client's own speechSynthesis — see below.

**Why ASR is the half that matters.** Typing Devanagari on a low-cost Android is
where users are lost; a parent who would happily send a voice note will abandon a
typed question. Recognition removes that barrier, and it has to run on the server
because a WhatsApp voice note never touches a browser.

**Why browser TTS is not a compromise.** `speechSynthesis` is synthesised
*on the device* on Android, iOS and macOS, all of which ship hi-IN voices. No
audio and no text leaves the phone. That is a better privacy position than any
cloud TTS, including the one this module would otherwise call — and it is the
opposite of `speechRecognition`, which Chrome implements by shipping audio to
Google, and which is therefore NOT used here now that a server ASR exists.

Rejected, with reasons, so this is not relitigated:

  * Cloudflare `@cf/deepgram/aura-*` — English and Spanish only. Fed Hindi it
    produces something Hindi-shaped: "होते हैं" comes back as "होटेइन", a
    retroflex ट where a dental त belongs. A parent reads this aloud to a child,
    so wrong pronunciation is worse than silence.
  * Cloudflare `@cf/myshell-ai/melotts` — returns a 3043 internal error for every
    input tried, English included.
  * Sarvam AI (Bulbul) — genuinely good Hindi and an Indian provider, but ₹100 of
    trial credit is not a free tier, and HANDOFF §2 rules out expiring credits.
    It is the right first call if server-side TTS ever becomes necessary, which
    is really only for spoken replies on WhatsApp.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import subprocess
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

GROQ_ASR_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_ASR_MODEL = os.environ.get("SAATHI_ASR_MODEL", "whisper-large-v3")
ASR_SAMPLE_RATE = 16_000
LANG = "hi"


class NotConfigured(RuntimeError):
    """No provider for this capability."""


class SpeechError(RuntimeError):
    pass


def _load_env() -> None:
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def _bhashini_ready() -> bool:
    _load_env()
    return bool(os.environ.get("BHASHINI_USER_ID")
                and os.environ.get("BHASHINI_API_KEY"))


def _groq_key() -> str:
    _load_env()
    k = os.environ.get("GROQ_API_KEY", "").strip()
    if not k:
        raise NotConfigured("GROQ_API_KEY is not set, so speech cannot be recognised.")
    return k


# --------------------------------------------------------------------- ASR
def _asr_groq(wav_bytes: bytes) -> dict:
    key = _groq_key()
    hfd, hpath = tempfile.mkstemp()
    afd, apath = tempfile.mkstemp(suffix=".wav")
    try:
        # Key in a header FILE, never argv.
        os.write(hfd, f"Authorization: Bearer {key}\n".encode())
        os.close(hfd)
        os.write(afd, wav_bytes)
        os.close(afd)
        t0 = time.time()
        r = subprocess.run(
            ["curl", "-sS", "--max-time", "60", GROQ_ASR_URL, "-H", f"@{hpath}",
             "-F", f"file=@{apath}", "-F", f"model={GROQ_ASR_MODEL}",
             # Naming the language stops Whisper hedging between Hindi and Urdu,
             # which are close enough acoustically that it sometimes returns
             # Nastaliq for perfectly ordinary Hindi speech.
             "-F", f"language={LANG}", "-F", "response_format=json"],
            capture_output=True, text=True)
        dt = time.time() - t0
    finally:
        os.unlink(hpath)
        os.unlink(apath)

    if r.returncode != 0:
        raise SpeechError(f"could not reach Groq: {r.stderr[:200]}")
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise SpeechError(f"Groq returned non-JSON: {r.stdout[:200]}") from None
    if "error" in d:
        raise SpeechError(str(d["error"])[:250])
    return {"text": (d.get("text") or "").strip(),
            "seconds": round(dt, 2), "provider": f"groq/{GROQ_ASR_MODEL}"}


def transcribe(audio_base64: str, sample_rate: int = ASR_SAMPLE_RATE) -> dict:
    """A recorded question -> Devanagari text.

    Whichever provider answers, the caller gets the same shape, and FR-2 then
    reads the transcript back before anything is answered — §8.3's point being
    that "एक बटा चार" misheard as "एक बटा चालीस" silently changes the question
    and the parent cannot detect it.
    """
    if os.environ.get("SAATHI_ASR", "").strip().lower() == "bhashini" or (
            _bhashini_ready() and os.environ.get("SAATHI_ASR", "") != "groq"):
        import bhashini

        out = bhashini.transcribe(audio_base64, sample_rate=sample_rate)
        out.setdefault("provider", "bhashini")
        return out
    return _asr_groq(base64.b64decode(audio_base64))


# --------------------------------------------------------------------- TTS
def speak(text: str) -> dict:
    """Answer text -> Hindi audio, when a server-side voice exists.

    Raises NotConfigured when none does, which is the normal case: the client
    then synthesises on-device, which costs nothing and sends nothing anywhere.
    """
    if _bhashini_ready():
        import bhashini

        out = bhashini.speak(text)
        out.setdefault("provider", "bhashini")
        return out
    raise NotConfigured(
        "No server-side Hindi voice is configured; the browser speaks the answer "
        "on-device instead. Add Bhashini credentials to change that.")


# ------------------------------------------------------------------ status
def available() -> dict:
    """What this box can actually do, per capability.

    Two capabilities, reported separately, because they are independently
    configured and the interface has to branch on each. Reporting one combined
    "voice: ok" hid the fact that recognition and synthesis come from different
    places.
    """
    asr: dict = {"ok": False, "provider": None, "reason": None}
    if _bhashini_ready() and os.environ.get("SAATHI_ASR", "") != "groq":
        asr = {"ok": True, "provider": "bhashini"}
    else:
        try:
            _groq_key()
            asr = {"ok": True, "provider": f"groq/{GROQ_ASR_MODEL}"}
        except NotConfigured as exc:
            asr["reason"] = str(exc)

    tts = ({"ok": True, "provider": "bhashini"} if _bhashini_ready()
           else {"ok": False, "provider": "browser",
                 "reason": "No server voice; the browser speaks on-device, "
                           "which sends nothing anywhere."})
    return {"asr": asr, "tts": tts,
            "ok": asr["ok"] or tts["ok"]}


if __name__ == "__main__":
    import sys

    print(json.dumps(available(), ensure_ascii=False, indent=2))
    if len(sys.argv) > 1:
        wav = pathlib.Path(sys.argv[1]).read_bytes()
        out = transcribe(base64.b64encode(wav).decode())
        print(f"\n  {out['provider']}  {out['seconds']}s\n  heard: {out['text']}")
