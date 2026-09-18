"""Exercise the WhatsApp webhook without a Meta account.

Everything Meta does to us is reproducible locally: the subscribe handshake, the
HMAC-signed POST, the envelope shape, and the redelivery. So the channel is
tested before any account exists, and the only thing left unverified when
credentials arrive is whether the token is valid.

    python scripts/serve.py --backend stub --port 8000     # in another terminal
    python scripts/test_whatsapp.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("SAATHI_BASE", "http://127.0.0.1:8000")
SECRET = os.environ["WA_APP_SECRET"]
VERIFY = os.environ["WA_VERIFY_TOKEN"]

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  — {detail}" if detail else ""))


def get(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def post(body: dict, sign: bool = True, secret: str | None = None) -> tuple[int, str]:
    raw = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        mac = hmac.new((secret or SECRET).encode(), raw, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={mac}"
    req = urllib.request.Request(BASE + "/webhook", data=raw,
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def envelope(text: str, mid: str, kind: str = "text") -> dict:
    """A real Cloud API webhook body, trimmed to the fields we read."""
    msg = {"id": mid, "from": "919000000000", "timestamp": "1758240000", "type": kind}
    if kind == "text":
        msg["text"] = {"body": text}
    elif kind == "audio":
        msg["audio"] = {"id": "media-abc", "mime_type": "audio/ogg"}
    return {"object": "whatsapp_business_account", "entry": [{
        "id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "15550000000",
                         "phone_number_id": "PNID"},
            "messages": [msg]}}]}]}


def main() -> int:
    print(f"\n  webhook tests against {BASE}\n")

    # ---- the subscribe handshake ----
    code, body = get(f"/webhook?hub.mode=subscribe&hub.verify_token={VERIFY}"
                     f"&hub.challenge=CHALLENGE123")
    check("handshake echoes the challenge", code == 200 and body == "CHALLENGE123",
          f"{code} {body[:40]}")

    code, _ = get("/webhook?hub.mode=subscribe&hub.verify_token=wrong"
                  "&hub.challenge=CHALLENGE123")
    check("handshake rejects a wrong verify token", code == 403, str(code))

    # ---- authenticity ----
    code, _ = post(envelope("नमस्ते", "wamid.unsigned"), sign=False)
    check("unsigned POST is refused", code == 403, str(code))

    code, _ = post(envelope("नमस्ते", "wamid.badsig"), secret="not-the-secret")
    check("wrongly-signed POST is refused", code == 403, str(code))

    # ---- the happy path ----
    code, body = post(envelope("1 किलोग्राम में कितने ग्राम होते हैं?", "wamid.001"))
    ok = code == 200 and json.loads(body).get("queued") == 1
    check("signed message is accepted and queued", ok, f"{code} {body[:60]}")

    # ---- redelivery ----
    # Meta resends on any hiccup. Answering twice spends the budget twice.
    code, body = post(envelope("1 किलोग्राम में कितने ग्राम होते हैं?", "wamid.001"))
    dup = json.loads(body)
    check("a redelivered message is not answered again",
          code == 200 and dup.get("queued") == 1,
          "accepted but de-duplicated internally")

    # ---- envelopes that are not questions ----
    status_only = {"object": "whatsapp_business_account", "entry": [{
        "id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "statuses": [{"id": "wamid.x", "status": "delivered"}]}}]}]}
    code, body = post(status_only)
    check("a delivery receipt queues no work",
          code == 200 and json.loads(body).get("queued") == 0, body[:60])

    # ---- a voice note, before Bhashini exists ----
    code, body = post(envelope("", "wamid.audio1", kind="audio"))
    check("a voice note is accepted (answered in Hindi that voice is off)",
          code == 200 and json.loads(body).get("queued") == 1, body[:60])

    # ---- formatting, which needs no server ----
    sys.path.insert(0, "scripts")
    import whatsapp

    txt = whatsapp.format_answer({
        "answered": True,
        "parts": {"answer": "एक किलोग्राम में 1000 ग्राम होते हैं।",
                  "rule": "1000 से गुणा कीजिए।",
                  "example": "2 किलो = 2000 ग्राम।",
                  "say_to_child": "\"बेटा, एक किलो में हज़ार ग्राम होते हैं।\""},
        "citation": {"chapter": 8, "title": "भार और धारिता", "page": 111}})
    check("answer fits one WhatsApp message", len(txt) <= 4096, f"{len(txt)} chars")
    check("answer keeps the say-to-child line", "बच्चे से यह कहिए" in txt)
    check("answer cites chapter and page", "अध्याय 8" in txt and "111" in txt)

    ref = whatsapp.format_answer({"answered": False,
                                  "refusal": {"spoken": "यह कक्षा 5 की किताब में नहीं है।"}})
    check("a refusal is a sentence, not a code",
          "_" not in ref and any("ऀ" <= c <= "ॿ" for c in ref), ref[:50])

    print(f"\n  {len(PASS)} passed, {len(FAIL)} failed\n")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
