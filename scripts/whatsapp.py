"""WhatsApp Cloud API — the surface the product is actually meant to live on.

§3.1's parent has WhatsApp and little else. The web chat exists because a Cloud
API test number can only message pre-approved recipients, so it demos to nobody;
this is the channel the real user would use.

**Cost, checked rather than assumed (19 Sep 2026).** There is no subscription fee.
Inbound messages are always free. Service replies inside the 24-hour customer
service window are free until 1 Oct 2026, after which each number gets 1,000 free
service messages per month. Every message this product sends is a service reply —
a parent asks, we answer within seconds — so the free allowance is the whole
envelope. Groq's ~90 answers/day (~2,700/month) means WhatsApp's 1,000/month
becomes the binding limit after October, which is still far more than a pilot
needs.

Four environment variables, all from the Meta app:

    WA_VERIFY_TOKEN     any string you invent; Meta echoes it back to prove the
                        webhook is yours
    WA_ACCESS_TOKEN     the access token for the phone number
    WA_PHONE_NUMBER_ID  the sending number's id (NOT the phone number itself)
    WA_APP_SECRET       used to verify each request really came from Meta

Absent, `available()` reports why and the web chat is unaffected.

Two things about this integration that are not obvious and will bite:

1. **Meta expects a 200 within seconds and retries if it does not get one.**
   Generation takes 6-16s. So the webhook acknowledges immediately and answers on
   a worker thread; replying inline would make Meta redeliver the same question
   two or three times and spend the daily Groq budget answering it repeatedly.

2. **Redelivery happens anyway** — network blips, Meta's own retries. Every
   message id is remembered, so the same question is never answered twice.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import subprocess
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

GRAPH = "https://graph.facebook.com/v21.0"
MAX_BODY = 4096          # WhatsApp's own text limit
SEEN_TTL = 3600          # how long a message id is remembered


class NotConfigured(RuntimeError):
    """No WhatsApp credentials. The channel is off; the web chat still works."""


class WhatsAppError(RuntimeError):
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


def _cfg(name: str) -> str:
    _load_env()
    v = os.environ.get(name, "").strip()
    if not v:
        raise NotConfigured(
            f"{name} is not set. Create a Meta app at developers.facebook.com, add "
            f"the WhatsApp product, and copy WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID, "
            f"WA_APP_SECRET and a WA_VERIFY_TOKEN of your choosing into .env.")
    return v


def available() -> dict:
    try:
        for k in ("WA_VERIFY_TOKEN", "WA_ACCESS_TOKEN",
                  "WA_PHONE_NUMBER_ID", "WA_APP_SECRET"):
            _cfg(k)
    except NotConfigured as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "number_id": _cfg("WA_PHONE_NUMBER_ID")}


# --------------------------------------------------------------- authenticity
def verify_signature(raw: bytes, header: str | None) -> bool:
    """Is this really from Meta?

    The webhook URL is public, so without this anyone who finds it can make the
    product answer questions on the daily Groq budget, or make it send messages.
    Compared with `compare_digest` because a plain `==` on an HMAC leaks the
    answer one byte at a time.
    """
    if not header or not header.startswith("sha256="):
        return False
    try:
        secret = _cfg("WA_APP_SECRET")
    except NotConfigured:
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def verify_challenge(params: dict) -> str | None:
    """Meta's one-time GET handshake. Returns the challenge to echo, or None."""
    if params.get("hub.mode") != "subscribe":
        return None
    try:
        if params.get("hub.verify_token") != _cfg("WA_VERIFY_TOKEN"):
            return None
    except NotConfigured:
        return None
    return params.get("hub.challenge")


# ------------------------------------------------------------------- parsing
def parse(payload: dict) -> list[dict]:
    """Pull the messages out of a webhook body.

    The envelope is deeply nested and carries delivery receipts and status
    updates as well as messages — answering a status update would be an expensive
    no-op, so anything without a `messages` array is ignored.
    """
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for m in value.get("messages", []):
                kind = m.get("type")
                rec = {"id": m.get("id"), "from": m.get("from"),
                       "type": kind, "text": None, "audio_id": None}
                if kind == "text":
                    rec["text"] = (m.get("text") or {}).get("body", "").strip()
                elif kind == "audio":
                    rec["audio_id"] = (m.get("audio") or {}).get("id")
                out.append(rec)
    return out


# ---------------------------------------------------------------- de-dup
_seen: dict[str, float] = {}
_seen_lock = threading.Lock()


def already_handled(message_id: str) -> bool:
    """Meta redelivers. Answering twice spends the budget twice."""
    now = time.time()
    with _seen_lock:
        for k, t in list(_seen.items()):
            if now - t > SEEN_TTL:
                del _seen[k]
        if message_id in _seen:
            return True
        _seen[message_id] = now
    return False


# -------------------------------------------------------------------- sending
def _post(path: str, body: dict) -> dict:
    token = _cfg("WA_ACCESS_TOKEN")
    hfd, hpath = tempfile.mkstemp()
    bfd, bpath = tempfile.mkstemp()
    try:
        # Token in a header FILE, never argv — argv is readable by every process
        # on the box and lands in shell history.
        os.write(hfd, f"Authorization: Bearer {token}\n".encode())
        os.close(hfd)
        os.write(bfd, json.dumps(body).encode())
        os.close(bfd)
        r = subprocess.run(
            ["curl", "-sS", "--max-time", "30", "-X", "POST", f"{GRAPH}/{path}",
             "-H", f"@{hpath}", "-H", "Content-Type: application/json",
             "--data-binary", f"@{bpath}"],
            capture_output=True, text=True)
    finally:
        os.unlink(hpath)
        os.unlink(bpath)
    if r.returncode != 0:
        raise WhatsAppError(f"could not reach Graph API: {r.stderr[:200]}")
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise WhatsAppError(f"Graph API returned non-JSON: {r.stdout[:200]}") from None
    if "error" in out:
        raise WhatsAppError(str(out["error"])[:300])
    return out


def send_text(to: str, body: str) -> dict:
    """One text message back to the parent."""
    body = body.strip()[:MAX_BODY]
    return _post(f"{_cfg('WA_PHONE_NUMBER_ID')}/messages", {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    })


# -------------------------------------------------------------- formatting
def format_answer(out: dict) -> str:
    """The four-part contract, as one WhatsApp message.

    The web card can use colour, numbering and a highlighted fourth part. Here
    there is only text, so the structure has to come from labels and WhatsApp's
    own *bold*. The say-to-child line still gets the visual weight, because it is
    the thing the parent is meant to use.
    """
    if not out.get("answered"):
        r = out.get("refusal") or {}
        line = r.get("spoken") or "मुझे इसका पक्का जवाब नहीं पता।"
        tail = "\n\nकुछ और पूछना हो तो पूछिए।"
        return line + tail

    p = out.get("parts") or {}
    c = out.get("citation") or {}
    bits = []
    if p.get("answer"):
        bits.append(p["answer"])
    if p.get("rule"):
        bits.append(f"*नियम:* {p['rule']}")
    if p.get("example"):
        bits.append(f"*उदाहरण:* {p['example']}")
    if p.get("say_to_child"):
        bits.append(f"*बच्चे से यह कहिए:*\n{p['say_to_child']}")
    if c.get("chapter"):
        bits.append(f"_किताब: अध्याय {c['chapter']} — {c.get('title','')}, "
                    f"पेज {c.get('page')}_")
    return "\n\n".join(bits)


if __name__ == "__main__":
    print(json.dumps(available(), ensure_ascii=False, indent=2))
