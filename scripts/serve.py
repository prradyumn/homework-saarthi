"""Public web chat — the demo surface (FR-8).

FR-8 is P0 and marked portfolio-critical for a specific reason: the WhatsApp Cloud
API test number can only message pre-approved recipients, so WhatsApp cannot be
shown to anyone who has not been added to it. The web chat is what makes the
product openable by a stranger with a link.

Deliberately built on the Python standard library — no Flask, no FastAPI, no npm.
This is a prototype on free tiers; a dependency that has to be installed before the
demo runs is a worse demo.

It implements the parts of the conversation contract that do not need Bhashini:

  FR-2  the interpretation is read back before answering, and answering requires
        confirmation. §8.3 calls this mandatory, because ASR mishearing a number
        ("एक बटा चार" as "एक बटा चालीस") silently changes the question, and one
        extra turn converts a silent wrong answer into a visible correction.
  FR-3  every answer cites chapter and page
  FR-4  below-threshold questions get the §8.1 refusal package, never a guess
  FR-5  the four-part answer contract
  FR-7  the post-answer prompt, which feeds the WPEC north star
  FR-10 the textbook page image, on request and with every refusal

Voice (FR-1, FR-6) needs Bhashini credentials; the interface is built for it and
says so plainly rather than pretending.

Run:  python scripts/serve.py            then open http://localhost:8000
      python scripts/serve.py --port 8080 --backend ollama
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

WEB = ROOT / "web"
PAGES = ROOT / "ingest" / "pages"

_state = {"backend": "groq", "ready": False, "error": None}
_lock = threading.Lock()

# Session memory, keyed by a browser-generated id. FR-9 keeps the last few turns;
# nothing is persisted to disk, because a prototype that quietly accumulates
# transcripts of real conversations is a data-governance problem (§14) rather than
# a feature.
_sessions: dict[str, dict] = {}
MAX_TURNS = 3


def check_voice() -> None:
    try:
        import bhashini

        _state["voice"] = bhashini.available()
        v = _state["voice"]
        print("  voice: " + ("Bhashini connected — " + str(v.get("services"))
                             if v["ok"] else "not connected (text only)"), flush=True)
    except Exception as exc:  # noqa: BLE001
        _state["voice"] = {"ok": False, "reason": str(exc)[:200]}


def warm() -> None:
    """Load the embedding model once at startup, not per request. BGE-M3 peaks
    around 3.3 GB on this machine, so a per-request load would be unusable."""
    try:
        from answer import retrieve

        retrieve("तैयारी")  # forces the model and index into memory
        _state["ready"] = True
        print("  models loaded — ready", flush=True)
    except Exception as exc:  # noqa: BLE001
        _state["error"] = str(exc)
        print(f"  WARMUP FAILED: {exc}", flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write(f"  {args[0]}\n")

    # ----------------------------------------------------------------- helpers
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    # --------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            html = (WEB / "index.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")

        if url.path == "/api/status":
            return self._json(200, {"ready": _state["ready"], "error": _state["error"],
                                    "backend": _state["backend"],
                                    "voice": _state.get("voice", {"ok": False})})

        # FR-10: the textbook page image, by printed page number
        if url.path.startswith("/page/"):
            try:
                n = int(url.path.rsplit("/", 1)[1].split(".")[0])
            except ValueError:
                return self._json(400, {"error": "bad page"})
            f = PAGES / f"p{n:03d}.png"
            if not f.exists():
                return self._json(404, {"error": f"page {n} not rendered"})
            return self._send(200, f.read_bytes(), "image/png")

        if url.path.startswith("/static/"):
            f = WEB / url.path[len("/static/"):]
            if f.exists() and f.is_file():
                ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                return self._send(200, f.read_bytes(), ctype)

        self._json(404, {"error": "not found"})

    # -------------------------------------------------------------------- POST
    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})

        if url.path == "/api/transcribe":
            return self._transcribe(payload)
        if url.path == "/api/speak":
            return self._speak(payload)
        if url.path == "/api/interpret":
            return self._interpret(payload)
        if url.path == "/api/answer":
            return self._answer(payload)
        if url.path == "/api/feedback":
            return self._feedback(payload)
        self._json(404, {"error": "not found"})

    # ---------------------------------------------------------------- FR-1
    def _transcribe(self, payload: dict) -> None:
        """Voice note -> text. The transcript then goes through FR-2's confirmation
        turn before anything is answered, which is the whole point: §8.3 warns that
        "एक बटा चार" (1/4) misheard as "एक बटा चालीस" (1/40) silently changes the
        question, and the parent cannot detect it. One visible turn converts a
        silent wrong answer into a correctable one."""
        import bhashini

        audio = payload.get("audio_base64") or ""
        if not audio:
            return self._json(400, {"error": "no audio"})
        # ~1 MB of base64 is roughly 45s of 16 kHz mono PCM; FR-1 allows 60s
        if len(audio) > 2_000_000:
            return self._json(400, {"error_hi": "आवाज़ का संदेश बहुत लंबा है, "
                                                "एक मिनट से कम रखिए।"})
        try:
            rate = int(payload.get("sample_rate") or bhashini.ASR_SAMPLE_RATE)
            out = bhashini.transcribe(audio, sample_rate=rate)
            print(f"  ASR {out['seconds']}s -> {out['text'][:60]!r}", flush=True)
            return self._json(200, out)
        except bhashini.NotConfigured as exc:
            return self._json(200, {"unavailable": True, "detail": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return self._json(200, {"error_hi": "आवाज़ समझ नहीं आई, फिर बोलिए "
                                                "या लिख दीजिए।", "detail": str(exc)[:200]})

    # ---------------------------------------------------------------- FR-6
    def _speak(self, payload: dict) -> None:
        """Answer text -> Hindi audio. §8.3: fluent Hindi speech does not imply
        fluent Devanagari reading, so an answer the parent cannot read is an
        answer they cannot use."""
        import bhashini

        text = (payload.get("text") or "").strip()
        if not text:
            return self._json(400, {"error": "no text"})
        try:
            out = bhashini.speak(text[:1200])
            print(f"  TTS {out['seconds']}s for {out['chars']} chars", flush=True)
            return self._json(200, out)
        except bhashini.NotConfigured as exc:
            return self._json(200, {"unavailable": True, "detail": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return self._json(200, {"error_hi": "आवाज़ बनाने में दिक्कत हुई।",
                                    "detail": str(exc)[:200]})

    # ---------------------------------------------------------------- FR-2
    def _interpret(self, payload: dict) -> None:
        """Read the question back before answering.

        With typed text there is nothing to mishear, so this could be skipped —
        but §8.3 makes it mandatory and open question 1 asks whether parents
        tolerate the extra turn. Keeping it in the text path is what makes that
        question answerable in the pilot instead of guessed at.
        """
        q = (payload.get("question") or "").strip()
        if not q:
            return self._json(400, {"error": "empty"})
        if len(q) > 500:
            return self._json(400, {"error": "too long"})

        from query_gate import pre_check

        pre = pre_check(q)
        return self._json(200, {
            "interpreted": q,
            "prompt_hi": "आपने पूछा:",
            "confirm_hi": "सही है?",
            # a pre-check that already wants clarification is surfaced here, at the
            # confirmation turn, which is the natural place to ask for it
            "clarify": None if pre["outcome"] == "pass" else pre["reason"],
        })

    # ------------------------------------------------------- FR-3/4/5/9
    def _answer(self, payload: dict) -> None:
        if not _state["ready"]:
            return self._json(503, {"error": "still loading the models, one moment"})

        q = (payload.get("question") or "").strip()
        sid = (payload.get("session") or "anon")[:64]
        if not q:
            return self._json(400, {"error": "empty"})

        from answer import answer

        t0 = time.time()
        try:
            with _lock:  # one model, one request at a time
                out = answer(q, backend=_state["backend"], verbose=False)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            friendly = ("आज के लिए मुफ़्त सीमा पूरी हो गई है, कल फिर कोशिश कीजिए।"
                        if "daily" in msg or "TPD" in msg else
                        "कुछ तकनीकी दिक्कत आ गई। थोड़ी देर बाद कोशिश कीजिए।")
            return self._json(200, {"answered": False, "error_hi": friendly,
                                    "detail": msg[:200]})

        sess = _sessions.setdefault(sid, {"turns": []})
        sess["turns"] = (sess["turns"] + [{"q": q, "answered": out.get("answered")}])[-MAX_TURNS:]

        out["elapsed"] = round(time.time() - t0, 2)
        out["refusal_line_hi"] = out.get("refusal", {}).get("spoken")
        return self._json(200, out)

    # ---------------------------------------------------------------- FR-7
    def _feedback(self, payload: dict) -> None:
        """Records accepted/not and explained/not — the two signals behind the
        §7.1 north star (WPEC) and the §7.2 answer-accepted rate.

        Kept in memory only. Instrumenting this properly is PostHog's job (§11.2);
        writing conversation transcripts to disk in a prototype would be a §14
        data-governance decision made by accident.
        """
        sid = (payload.get("session") or "anon")[:64]
        sess = _sessions.setdefault(sid, {"turns": [], "feedback": []})
        sess.setdefault("feedback", []).append({
            "understood": bool(payload.get("understood")),
            "explained": bool(payload.get("explained")),
            "at": time.time(),
        })
        n = sum(1 for f in sess["feedback"] if f["explained"])
        print(f"  feedback: understood={payload.get('understood')} "
              f"explained={payload.get('explained')}  (session WPEC={n})", flush=True)
        return self._json(200, {"ok": True, "session_explained_count": n})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--backend", default="groq", choices=["groq", "ollama"])
    args = ap.parse_args()
    _state["backend"] = args.backend

    if not (WEB / "index.html").exists():
        print(f"missing {WEB / 'index.html'}", file=sys.stderr)
        return 1

    threading.Thread(target=warm, daemon=True).start()
    threading.Thread(target=check_voice, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"\n  Homework Saathi — http://{args.host}:{args.port}")
    print(f"  backend: {args.backend}   (loading models in the background…)\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
