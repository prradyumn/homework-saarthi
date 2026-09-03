"""Bhashini (Government of India) ASR and TTS — the voice loop (FR-1, FR-6).

§8.4 makes the reason for choosing Bhashini explicit: it is a sovereignty and
data-governance decision as much as a cost one. This is a child's homework passing
through a speech service, and routing it through Indian public language
infrastructure rather than a US frontier API is the defensible choice.

The API is a two-step ULCA flow:

  1. config   POST meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline
              headers: userID, ulcaApiKey
              returns the inference endpoint, an inference key, and a serviceId
              per task
  2. compute  POST that returned endpoint with that returned key

Public examples hardcode the endpoint and inference key. This reads both from the
config response, because a hardcoded third-party key is someone else's credential
and will rotate without warning.

The config response is cached on disk: it changes rarely, and paying a second
round trip on every question would eat the §7.3 budget of 20s p95 for the whole
voice round trip.

Credentials — free for non-commercial use — come from the Bhashini ULCA portal
and go in .env as BHASHINI_USER_ID and BHASHINI_API_KEY. With them absent every
function raises NotConfigured, which the web chat surfaces as "voice not
connected yet" rather than as a crash.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "ingest" / "bhashini_pipeline.json"

CONFIG_URL = "https://meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline"
# MeitY's published pipeline id. Overridable, because pipeline ids are data.
PIPELINE_ID = os.environ.get("BHASHINI_PIPELINE_ID", "64392f96daac500b55c543cd")

LANG = "hi"
ASR_SAMPLE_RATE = 16_000     # what the browser recorder is asked to produce
TTS_SAMPLE_RATE = 22_050
TTS_GENDER = os.environ.get("BHASHINI_TTS_GENDER", "female")
CACHE_TTL_SECONDS = 24 * 3600


class NotConfigured(RuntimeError):
    """No Bhashini credentials. Voice is unavailable; text still works."""


class BhashiniError(RuntimeError):
    pass


def _creds() -> tuple[str, str]:
    uid = os.environ.get("BHASHINI_USER_ID")
    key = os.environ.get("BHASHINI_API_KEY")
    if not uid or not key:
        raise NotConfigured(
            "Set BHASHINI_USER_ID and BHASHINI_API_KEY in .env — register free for "
            "non-commercial use at bhashini.gov.in (ULCA portal)."
        )
    return uid, key


def _post(url: str, headers: dict[str, str], body: dict, timeout: int = 60) -> dict:
    args = ["curl", "-sS", "--max-time", str(timeout), url, "-H",
            "Content-Type: application/json"]
    for k, v in headers.items():
        args += ["-H", f"{k}: {v}"]
    args += ["-d", "@-"]
    proc = subprocess.run(args, input=json.dumps(body), capture_output=True, text=True)
    if proc.returncode != 0:
        raise BhashiniError(f"network: {proc.stderr.strip()[:200]}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        raise BhashiniError(f"unparseable response: {proc.stdout[:200]!r}") from None


def pipeline_config(refresh: bool = False) -> dict:
    """Resolve the inference endpoint, key and per-task serviceIds. Cached."""
    if not refresh and CACHE.exists():
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - cached.get("fetched_at", 0) < CACHE_TTL_SECONDS:
                return cached
        except (json.JSONDecodeError, OSError):
            pass

    uid, key = _creds()
    data = _post(
        CONFIG_URL, {"userID": uid, "ulcaApiKey": key},
        {"pipelineTasks": [{"taskType": "asr"}, {"taskType": "tts"}],
         "pipelineRequestConfig": {"pipelineId": PIPELINE_ID}},
    )
    if "pipelineResponseConfig" not in data:
        raise BhashiniError(f"config call failed: {str(data)[:300]}")

    endpoint = data.get("pipelineInferenceAPIEndPoint") or {}
    inference_key = endpoint.get("inferenceApiKey") or {}
    services: dict[str, str] = {}
    for task in data["pipelineResponseConfig"]:
        ttype = task.get("taskType")
        for cfg in task.get("config") or []:
            sid = cfg.get("serviceId")
            # prefer a service that actually offers Hindi
            langs = cfg.get("language") or {}
            if sid and (langs.get("sourceLanguage") in (None, LANG)):
                services.setdefault(ttype, sid)
        if ttype not in services and task.get("config"):
            services[ttype] = task["config"][0].get("serviceId", "")

    resolved = {
        "callback_url": endpoint.get("callbackUrl"),
        "auth_header": inference_key.get("name"),
        "auth_value": inference_key.get("value"),
        "services": services,
        "fetched_at": time.time(),
    }
    missing = [k for k in ("callback_url", "auth_header", "auth_value") if not resolved[k]]
    if missing:
        raise BhashiniError(f"config response missing {missing}: {str(data)[:300]}")
    CACHE.write_text(json.dumps(resolved, ensure_ascii=False, indent=2), encoding="utf-8")
    return resolved


def _compute(task: dict, input_data: dict) -> dict:
    cfg = pipeline_config()
    return _post(
        cfg["callback_url"], {cfg["auth_header"]: cfg["auth_value"]},
        {"pipelineTasks": [task], "inputData": input_data},
    )


def transcribe(wav_base64: str, sample_rate: int = ASR_SAMPLE_RATE) -> dict:
    """Hindi speech -> text (FR-1). `wav_base64` is a 16-bit PCM WAV, mono.

    The browser encodes WAV in JavaScript rather than sending WebM/Opus, so no
    server-side transcode (and no ffmpeg dependency) is needed.
    """
    cfg = pipeline_config()
    t0 = time.time()
    out = _compute(
        {"taskType": "asr",
         "config": {"language": {"sourceLanguage": LANG},
                    "serviceId": cfg["services"].get("asr", ""),
                    "audioFormat": "wav",
                    "samplingRate": sample_rate}},
        {"audio": [{"audioContent": wav_base64}]},
    )
    try:
        text = out["pipelineResponse"][0]["output"][0]["source"]
    except (KeyError, IndexError, TypeError):
        raise BhashiniError(f"no transcript in response: {str(out)[:300]}") from None
    return {"text": text.strip(), "seconds": round(time.time() - t0, 2)}


def speak(text: str, sample_rate: int = TTS_SAMPLE_RATE) -> dict:
    """Text -> Hindi speech (FR-6). Returns base64 audio for the browser.

    §8.3 makes voice the default rather than an add-on: fluent Hindi speech does
    not imply fluent Devanagari reading, so an answer the parent cannot read is
    an answer they cannot use.
    """
    cfg = pipeline_config()
    t0 = time.time()
    out = _compute(
        {"taskType": "tts",
         "config": {"language": {"sourceLanguage": LANG},
                    "serviceId": cfg["services"].get("tts", ""),
                    "gender": TTS_GENDER,
                    "samplingRate": sample_rate}},
        {"input": [{"source": text}]},
    )
    try:
        audio = out["pipelineResponse"][0]["audio"][0]["audioContent"]
    except (KeyError, IndexError, TypeError):
        raise BhashiniError(f"no audio in response: {str(out)[:300]}") from None
    return {"audio_base64": audio, "chars": len(text),
            "seconds": round(time.time() - t0, 2)}


def available() -> dict:
    """Whether voice can be offered, for the interface to branch on."""
    try:
        _creds()
    except NotConfigured as exc:
        return {"ok": False, "reason": str(exc)}
    try:
        cfg = pipeline_config()
        return {"ok": True, "services": cfg["services"]}
    except (BhashiniError, OSError) as exc:
        return {"ok": False, "reason": str(exc)[:200]}


if __name__ == "__main__":
    import sys

    status = available()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if status["ok"] and len(sys.argv) > 1 and sys.argv[1] == "--speak":
        r = speak("नमस्ते, मैं होमवर्क साथी हूँ।")
        pathlib.Path("/tmp/bhashini_test.wav").write_bytes(
            base64.b64decode(r["audio_base64"]))
        print(f"wrote /tmp/bhashini_test.wav in {r['seconds']}s")
