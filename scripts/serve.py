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
import os
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

_state = {"backend": "groq", "ready": False, "error": None,
          "vision": {"ok": False, "reason": "not checked"}}
_lock = threading.Lock()

# Session memory, keyed by a browser-generated id. FR-9 keeps the last few turns;
# nothing is persisted to disk, because a prototype that quietly accumulates
# transcripts of real conversations is a data-governance problem (§14) rather than
# a feature.
_sessions: dict[str, dict] = {}
MAX_TURNS = 3


PAGE_CACHE_SECONDS = 60 * 60 * 24 * 30   # a textbook page is immutable


def check_vision() -> None:
    """Whether textbook figures can be read, so the interface can say so."""
    try:
        import vision

        _state["vision"] = vision.available()
        v = _state["vision"]
        print("  figures: " + (f"readable via {v.get('model')}" if v["ok"]
                               else "not connected (chart questions refused)"),
              flush=True)
    except Exception as exc:  # noqa: BLE001
        _state["vision"] = {"ok": False, "reason": str(exc)[:200]}


def check_whatsapp() -> None:
    try:
        import whatsapp

        _state["whatsapp"] = whatsapp.available()
        print("  whatsapp: " + ("connected" if _state["whatsapp"]["ok"]
                                else "not connected (web chat only)"), flush=True)
    except Exception as exc:  # noqa: BLE001
        _state["whatsapp"] = {"ok": False, "reason": str(exc)[:200]}


def check_voice() -> None:
    """Recognition and synthesis are configured independently and reported apart.

    One combined "voice: ok" hid that they come from different places — the
    server now recognises via Groq Whisper while the browser synthesises
    on-device, and the interface has to branch on each separately.
    """
    try:
        import speech

        _state["voice"] = speech.available()
        v = _state["voice"]
        print(f"  speech in : {v['asr']['provider'] or 'none'}", flush=True)
        print(f"  speech out: {v['tts']['provider'] or 'none'}"
              + (" (on-device)" if v["tts"]["provider"] == "browser" else ""),
              flush=True)
    except Exception as exc:  # noqa: BLE001
        _state["voice"] = {"ok": False, "asr": {"ok": False}, "tts": {"ok": False},
                           "reason": str(exc)[:200]}


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
    def _send(self, code: int, body: bytes, ctype: str,
              cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
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

        # A container host needs a liveness probe that answers before BGE-M3 has
        # finished loading — otherwise the platform kills the box during warm-up
        # and the demo never starts. This says "the process is up"; /api/status
        # says "the models are ready". They are different questions.
        if url.path == "/api/health":
            return self._json(200, {"ok": True, "ready": _state["ready"]})

        # WhatsApp's subscribe handshake. Meta GETs this once with a challenge
        # and expects it echoed back verbatim as plain text.
        if url.path == "/webhook":
            import whatsapp

            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            challenge = whatsapp.verify_challenge(params)
            if challenge is None:
                return self._send(403, b"forbidden", "text/plain; charset=utf-8")
            return self._send(200, challenge.encode(), "text/plain; charset=utf-8")

        if url.path == "/api/status":
            import pagesource

            return self._json(200, {"ready": _state["ready"], "error": _state["error"],
                                    "backend": _state["backend"],
                                    "voice": _state.get("voice", {"ok": False}),
                                    "vision": _state.get("vision", {"ok": False}),
                                    "whatsapp": _state.get("whatsapp", {"ok": False}),
                                    "pages": pagesource.cache_state()})

        # FR-10: the textbook page image, by printed page number.
        #
        # `pagesource` decides where the bytes come from — a local render here, or
        # NCERT's own server on a deployed box, which is what keeps the deployed
        # demo from republishing a book we are told not to republish. Either way
        # the parent gets a phone-sized JPEG under a hard byte budget, because §3.1
        # says their data is metered.
        if url.path.startswith("/page/"):
            try:
                n = int(url.path.rsplit("/", 1)[1].split(".")[0])
            except ValueError:
                return self._json(400, {"error": "bad page"})
            import pagesource

            try:
                data, ctype = pagesource.for_web(n)
            except pagesource.PageUnavailable as exc:
                # The parent asked to see the page and we could not produce it.
                # Say so in Hindi; never leave a broken image in the card.
                return self._json(404, {
                    "error_hi": "किताब का यह पेज अभी नहीं दिखा पा रहा हूँ।",
                    "detail": str(exc)[:200]})
            return self._send(200, data, ctype,
                              cache=f"public, max-age={PAGE_CACHE_SECONDS}, immutable")

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
        raw = self.rfile.read(length) or b"{}"
        # The HMAC is over the exact bytes Meta sent, so keep them: re-serialising
        # the parsed object produces different bytes and a signature that never
        # matches.
        self._raw_body = raw
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json", "error_hi": "कुछ गड़बड़ हो गई, फिर कोशिश कीजिए।"})

        if url.path == "/webhook":
            return self._whatsapp(payload)
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
        import speech

        audio = payload.get("audio_base64") or ""
        if not audio:
            return self._json(400, {"error_hi": "आवाज़ रिकॉर्ड नहीं हुई, फिर बोलिए।"})
        # ~1 MB of base64 is roughly 45s of 16 kHz mono PCM; FR-1 allows 60s
        if len(audio) > 2_000_000:
            return self._json(400, {"error_hi": "आवाज़ का संदेश बहुत लंबा है, "
                                                "एक मिनट से कम रखिए।"})
        try:
            rate = int(payload.get("sample_rate") or speech.ASR_SAMPLE_RATE)
            out = speech.transcribe(audio, sample_rate=rate)
            print(f"  ASR [{out.get('provider')}] {out['seconds']}s "
                  f"-> {out['text'][:60]!r}", flush=True)
            return self._json(200, out)
        except speech.NotConfigured as exc:
            return self._json(200, {"unavailable": True, "detail": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return self._json(200, {"error_hi": "आवाज़ समझ नहीं आई, फिर बोलिए "
                                                "या लिख दीजिए।", "detail": str(exc)[:200]})

    # ---------------------------------------------------------------- FR-6
    def _speak(self, payload: dict) -> None:
        """Answer text -> Hindi audio. §8.3: fluent Hindi speech does not imply
        fluent Devanagari reading, so an answer the parent cannot read is an
        answer they cannot use."""
        import speech

        text = (payload.get("text") or "").strip()
        if not text:
            return self._json(400, {"error_hi": "सुनाने के लिए कुछ नहीं मिला।"})
        try:
            out = speech.speak(text[:1200])
            print(f"  TTS {out['seconds']}s for {out.get('chars', len(text))} chars",
                  flush=True)
            return self._json(200, out)
        except speech.NotConfigured as exc:
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
            return self._json(400, {"error_hi": "पहले सवाल लिखिए।"})
        if len(q) > 500:
            return self._json(400, {"error_hi": "सवाल बहुत लंबा है — छोटा करके पूछिए।"})

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
            return self._json(503, {"error_hi": "एक मिनट रुकिए, तैयारी हो रही है…"})

        q = (payload.get("question") or "").strip()
        sid = (payload.get("session") or "anon")[:64]
        if not q:
            return self._json(400, {"error_hi": "पहले सवाल लिखिए।"})

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
        # so a stubbed answer can never be mistaken for a real one
        out["backend"] = _state["backend"]
        out["refusal_line_hi"] = out.get("refusal", {}).get("spoken")
        return self._json(200, out)

    # ------------------------------------------------------------- WhatsApp
    def _whatsapp(self, payload: dict) -> None:
        """Take the message, say 200, and answer on a worker thread.

        Meta expects an acknowledgement within seconds and redelivers anything
        slower. Generation takes 6-16s, so replying inline would make Meta send
        the same question two or three times and spend the daily Groq budget
        answering it repeatedly. Acknowledge first, work afterwards.
        """
        import whatsapp

        if not whatsapp.verify_signature(getattr(self, "_raw_body", b""),
                                         self.headers.get("X-Hub-Signature-256")):
            # The URL is public. Without this, anyone who finds it can spend the
            # budget or make the product send messages.
            return self._send(403, b"bad signature", "text/plain; charset=utf-8")

        try:
            messages = whatsapp.parse(payload)
        except Exception as exc:  # noqa: BLE001
            print(f"  wa: unparseable webhook: {exc}", flush=True)
            messages = []

        # 200 now, before any work. Meta only needs to know we received it.
        self._json(200, {"ok": True, "queued": len(messages)})

        for m in messages:
            if not m.get("id") or whatsapp.already_handled(m["id"]):
                continue
            threading.Thread(target=self._whatsapp_reply, args=(m,),
                             daemon=True).start()

    def _whatsapp_reply(self, m: dict) -> None:
        import whatsapp

        to = m.get("from")
        try:
            if m.get("type") == "audio":
                # FR-1 needs Bhashini to turn a voice note into text. Until those
                # credentials exist, say so in Hindi rather than going quiet —
                # silence on WhatsApp reads as broken.
                return self._wa_send(to, "अभी मैं आवाज़ नहीं समझ पाता। "
                                         "सवाल लिखकर भेज दीजिए।")
            q = (m.get("text") or "").strip()
            if not q:
                return self._wa_send(to, "सवाल लिखकर भेजिए।")
            if len(q) > 500:
                return self._wa_send(to, "सवाल बहुत लंबा है — छोटा करके पूछिए।")
            if not _state["ready"]:
                return self._wa_send(to, "एक मिनट रुकिए, तैयारी हो रही है…")

            from answer import answer

            t0 = time.time()
            with _lock:
                out = answer(q, backend=_state["backend"], verbose=False)
            print(f"  wa: {'answered' if out.get('answered') else 'refused'} "
                  f"in {time.time() - t0:.1f}s", flush=True)
            self._wa_send(to, whatsapp.format_answer(out))
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            friendly = ("आज के लिए मुफ़्त सीमा पूरी हो गई है, कल फिर कोशिश कीजिए।"
                        if "daily" in msg or "TPD" in msg else
                        "कुछ तकनीकी दिक्कत आ गई। थोड़ी देर बाद कोशिश कीजिए।")
            print(f"  wa: FAILED {msg[:160]}", flush=True)
            self._wa_send(to, friendly)

    @staticmethod
    def _wa_send(to: str, body: str) -> None:
        import whatsapp

        try:
            whatsapp.send_text(to, body)
        except Exception as exc:  # noqa: BLE001
            print(f"  wa: could not send: {str(exc)[:160]}", flush=True)

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
    # A container is configured by environment, not by argv — the flags stay for
    # this laptop and win when both are given. HF Spaces sets PORT to 7860.
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--backend", default=os.environ.get("SAATHI_BACKEND", "groq"),
                    choices=["groq", "gemini", "ollama", "stub"],
                    help="stub = canned generation, for UI tests; spends no tokens")
    args = ap.parse_args()
    _state["backend"] = args.backend

    if not (WEB / "index.html").exists():
        print(f"missing {WEB / 'index.html'}", file=sys.stderr)
        return 1

    threading.Thread(target=warm, daemon=True).start()
    threading.Thread(target=check_voice, daemon=True).start()
    threading.Thread(target=check_vision, daemon=True).start()
    threading.Thread(target=check_whatsapp, daemon=True).start()
    # Pull the chapter PDFs into this box's cache in the background. ncert.nic.in
    # goes down for minutes at a time, and FR-10 should not depend on it being up
    # at the exact moment a parent taps "पेज देखिए".
    import pagesource

    pagesource.warm_cache()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"\n  Homework Saathi — http://{args.host}:{args.port}")
    print(f"  backend: {args.backend}   (loading models in the background…)\n")
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
