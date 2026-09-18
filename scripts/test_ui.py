"""Drive the web chat in a real browser (Playwright) and assert what a parent sees.

Everything before this was tested through curl, which exercises the server and
none of the interface. The interface is where the product actually is: the four
labelled parts, the confirmation turn, the page image, the feedback prompt. A
JavaScript error in any of those is invisible to curl and fatal to the demo.

This also catches the class of bug that only a browser has: console exceptions,
a button wired to nothing, a Devanagari font that never loaded, text clipped out
of its container.

Screenshots are written to /tmp/ui/ so the rendering can be inspected rather
than assumed.

Run (with the server already up on :8000):
    python scripts/test_ui.py
    python scripts/test_ui.py --headed      # watch it happen
"""

from __future__ import annotations

import argparse
import pathlib
import sys

BASE = "http://127.0.0.1:8000"


def require_free_backend() -> str:
    """Refuse to run against a metered backend unless --live is given.

    Nothing previously stopped this suite from calling the real model, and it
    quietly spent 197,907 of the free tier's 200,000 daily tokens — which then
    aborted the conformance run at 1 of 30 questions. The interface is what this
    file tests; real generation is eval_generation.py's job.

        python scripts/serve.py --backend stub    # then run this
        python scripts/test_ui.py --live          # deliberately use the real model
    """
    import json as _json
    import urllib.request

    with urllib.request.urlopen(f"{BASE}/api/status", timeout=10) as r:
        backend = _json.loads(r.read()).get("backend")
    if backend == "stub" or "--live" in sys.argv:
        return backend
    print(f"\n  REFUSING TO RUN: the server at {BASE} is using the '{backend}' "
          f"backend.\n"
          f"  Every run of this suite would spend real tokens from the free "
          f"tier's\n  200,000/day, which is the budget the conformance eval "
          f"needs.\n\n"
          f"    restart it as:  python scripts/serve.py --backend stub\n"
          f"    or force it:    python scripts/test_ui.py --live\n")
    raise SystemExit(2)
SHOTS = pathlib.Path("/tmp/ui")

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{('  — ' + detail) if detail else ''}",
          flush=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--question", default="1 किलोग्राम में कितने ग्राम होते हैं?")
    ap.add_argument("--live", action="store_true",
                    help="allow a metered backend; spends real tokens")
    args = ap.parse_args()
    backend = require_free_backend()
    print(f"  backend: {backend}"
          + ("   (real tokens being spent)" if backend != "stub" else "   (free)"))
    SHOTS.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(
            viewport={"width": 430, "height": 900},   # a phone, which is the real device
            locale="hi-IN",
            permissions=["microphone"],
        )
        page = ctx.new_page()

        # A console error is a broken demo; collect them all and assert at the end.
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)

        page.goto(BASE, wait_until="domcontentloaded")
        check("page loads", page.title() == "होमवर्क साथी", f"title={page.title()!r}")

        # The Devanagari webfont must actually arrive, or the whole UI silently
        # falls back to a system face. Wait for document.fonts.ready first: checking
        # at domcontentloaded reported a false failure, because the font was still
        # in flight. Then confirm it is really APPLIED by measuring text width
        # against a known-different family — fonts.check() can be optimistic.
        page.wait_for_function("() => document.fonts.status === 'loaded'", timeout=30_000)
        widths = page.evaluate("""() => {
            const s = document.createElement('span');
            s.textContent = 'गणित मेला';
            s.style.cssText = 'font-size:40px;position:absolute;visibility:hidden';
            s.style.fontFamily = '"IBM Plex Sans Devanagari"';
            document.body.appendChild(s);
            const plex = s.offsetWidth;
            s.style.fontFamily = 'serif';
            const serif = s.offsetWidth;
            s.remove();
            return { plex, serif };
        }""")
        check("Devanagari webfont loaded and applied", widths["plex"] != widths["serif"],
              f"plex={widths['plex']}px serif={widths['serif']}px")

        # Wait on the status DOT's class, not on the status text.
        #
        # Waiting for text containing "तैयार" (ready) also matched
        # "तैयार हो रहा है…" (still getting ready), so the test charged ahead
        # before the models had loaded, got a 503 from /api/answer and recorded it
        # as a refusal. Earlier runs only passed because the server happened to be
        # warm already — the classic test that passes for the wrong reason.
        page.wait_for_selector("#dot.ready", timeout=240_000)
        status = page.inner_text("#statusText")
        check("status reports ready", "तैयार" in status and "हो रहा" not in status, status)

        mode = page.evaluate("() => VOICE_MODE")
        # Recognition and synthesis are picked independently now: the server
        # recognises (Groq Whisper) while the browser synthesises on-device.
        modes = page.evaluate("() => ({asr: ASR_MODE, tts: TTS_MODE})")
        check("a recognition provider was selected",
              modes["asr"] in ("server", "browser"), f"asr={modes['asr']}")
        check("a synthesis provider was selected",
              modes["tts"] in ("server", "browser"), f"tts={modes['tts']}")
        page.screenshot(path=str(SHOTS / "01-idle.png"), full_page=True)

        # ---- example chips should exist and be clickable ----
        chips = page.locator("#examples button")
        check("example questions offered", chips.count() >= 3, f"{chips.count()} chips")

        # ---- ask a question by typing ----
        page.fill("#input", args.question)
        page.click("#send")

        # ---- FR-2: the confirmation turn must appear BEFORE any answer ----
        page.wait_for_selector(".confirm", timeout=30_000)
        confirm_text = page.inner_text(".confirm")
        check("FR-2 confirmation turn shown", "आपने पूछा" in confirm_text)
        check("FR-2 quotes the question back", args.question[:18] in confirm_text)
        answered_early = page.locator(".part").count()
        check("FR-2 blocks the answer until confirmed", answered_early == 0,
              f"{answered_early} parts visible before confirming")
        page.screenshot(path=str(SHOTS / "02-confirm.png"), full_page=True)

        page.click("[data-yes]")

        # ---- FR-5: four labelled parts ----
        #
        # An answer and a refusal both render `.part`, so the two outcomes have to
        # be told apart before asserting anything. Without that, a run where the
        # contract validator happened to reject the answer counted one "part" and
        # then spent 30 seconds waiting for labels that a refusal never has — a
        # flaky test that blamed the interface for a generation outcome.
        #
        # It is also a real finding: the same question does not always clear the
        # contract, because generation runs at temperature 0.2. The test therefore
        # reports which outcome it got instead of assuming.
        page.wait_for_selector(".card .part", timeout=120_000)
        page.wait_for_timeout(600)
        refused = page.locator(".card.refusal").count() > 0
        parts = page.locator(".card:not(.refusal) .part")
        n = parts.count()

        if refused:
            check("answered or refused (this run: REFUSED)", True,
                  "not a UI fault — generation declined or failed the contract; "
                  "rerun for the answered path")
        else:
            check("FR-5 four parts rendered", n == 4, f"{n} parts")
            # The card now leads with the payoff, so the old "every label has a
            # digit" check no longer describes the design: the say-to-child line
            # is the headline, not step four of anything, and is deliberately
            # unnumbered. The invariants that actually matter are that all four
            # parts are present and labelled, that the reasoning trio keeps its
            # 1-2-3 sequence, and that the payoff comes FIRST.
            labels = page.locator(".part-label").all_inner_texts()[:4]
            check("all four parts are labelled",
                  len(labels) == 4 and all(l.strip() for l in labels), str(labels))
            check("the reasoning trio stays numbered in order",
                  [l.strip()[0] for l in labels[1:4]] == ["1", "2", "3"],
                  str(labels[1:4]))
            check("exactly one say-to-child line", page.locator(".part.say").count() == 1)
            check("the payoff is rendered FIRST, before the reasoning",
                  page.evaluate("""() => {
                      const parts = [...document.querySelectorAll('.card .part')];
                      return parts.length > 0 && parts[0].classList.contains('say');
                  }"""))

        # ---- FR-3: citation ----
        cite = page.locator(".cite").first
        cite_text = cite.inner_text() if cite.count() else ""
        if not refused:
            check("FR-3 cites chapter and page",
                  "अध्याय" in cite_text and "पेज" in cite_text, cite_text[:70])

            # §3.1 makes reading effort the single most consequential user
            # attribute, and the answer is text the parent reads ALOUD. Assert a
            # legibility floor so it cannot drift back to chat-default sizing.
            sizes = page.evaluate("""() => {
                const px = (sel) => {
                    const e = document.querySelector(sel);
                    return e ? parseFloat(getComputedStyle(e).fontSize) : 0;
                };
                return { body: px('body'), answer: px('.part p'), say: px('.part.say p') };
            }""")
            check("answer text is large enough to read aloud (>=19px)",
                  sizes["answer"] >= 19 and sizes["say"] >= 19, str(sizes))

            # The sticky header must never cover answer text. Measured with element
            # rects: a full-page screenshot renders position:sticky at its scroll
            # offset and so cannot show this.
            covered = page.evaluate("""() => {
                const h = document.querySelector('header').getBoundingClientRect();
                return [...document.querySelectorAll('.part p')]
                    .filter(p => { const r = p.getBoundingClientRect();
                                   return r.top < h.bottom && r.bottom > h.top; }).length;
            }""")
            check("sticky header covers no answer text", covered == 0,
                  f"{covered} parts behind the header")

        # nothing may overflow the phone width
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check("no horizontal overflow on a phone", overflow <= 1, f"{overflow}px")
        page.screenshot(path=str(SHOTS / "03-answer.png"), full_page=True)

        # ---- FR-10: the real textbook page ----
        if page.locator("[data-page]").count() == 0:
            check("FR-10 page image offered", False, "no page button on this run")
            page.screenshot(path=str(SHOTS / "04-no-page.png"), full_page=True)
            browser.close()
            print(f"\n  {len(PASS)} passed, {len(FAIL)} failed  (partial: run was refused)")
            print(f"  screenshots -> {SHOTS}")
            return 1 if FAIL else 0
        page.click("[data-page]")
        page.wait_for_selector(".page-img", timeout=30_000)
        # naturalWidth is 0 until the bitmap has decoded — checking straight after
        # the element appears reported a false 0x0 failure.
        page.wait_for_function(
            """() => { const i = document.querySelector('.page-img');
                       return i && i.complete && i.naturalWidth > 0; }""",
            timeout=60_000)
        dims = page.evaluate("""() => { const i = document.querySelector('.page-img');
            return { w: i.naturalWidth, h: i.naturalHeight }; }""")
        check("FR-10 page image actually renders", dims["w"] > 100 and dims["h"] > 100,
              f"{dims['w']}x{dims['h']}")
        # §3.1: metered, intermittent data. The page image is the heaviest thing we
        # send, so its weight is asserted rather than left to drift.
        weight = page.evaluate("""async () => {
            const r = await fetch(document.querySelector('.page-img').src);
            return (await r.blob()).size; }""")
        check("FR-10 page image is data-cheap (<150 KB)", weight < 150_000,
              f"{weight/1024:.0f} KB")
        page.screenshot(path=str(SHOTS / "04-page-image.png"), full_page=True)
        page.click("[data-page]")   # collapse again

        # ---- FR-6: the listen button exists and does something ----
        listen = page.locator("[data-speak]")
        check("FR-6 listen control offered", listen.count() == 1)

        # ---- FR-7: the feedback prompt, which feeds the north star ----
        fb = page.locator("[data-fb]")
        if fb.count() == 0:
            check("FR-7 post-answer prompt shown", False, "absent (refused run)")
        else:
            check("FR-7 post-answer prompt shown", "समझा दिया" in fb.inner_text())
            page.click("[data-exp='1']")
            page.wait_for_timeout(400)
            page.wait_for_selector(".fb-done", timeout=10_000)
            check("FR-7 records the answer", "शुक्रिया" in page.inner_text(".fb-done"))

        # ---- FR-4: a refusal must show the §8.1 package, not a bare no ----
        # No confirmation turn is expected here any more. This question is caught
        # by pre_check on its beyond-Class-5 vocabulary, so the outcome is known
        # before retrieval runs, and asking the parent to confirm a question that
        # is about to be refused with the same sentence just says it twice.
        # A SPOKEN question still gets its confirmation — a mangled transcript is
        # a real reason the gate trips.
        before = page.locator(".confirm").count()
        page.fill("#input", "द्विघात समीकरण का सूत्र क्या है?")
        page.click("#send")
        page.wait_for_selector(".refusal", timeout=120_000)
        check("out-of-syllabus refusal needs no confirmation turn",
              page.locator(".confirm").count() == before,
              f"{page.locator('.confirm').count() - before} new confirm cards")
        ref = page.inner_text(".refusal")
        check("FR-4 refuses out-of-syllabus", "कक्षा 5" in ref or "नहीं" in ref, ref[:70])
        check("FR-4 refusal offers no irrelevant page",
              page.locator(".refusal [data-page]").count() == 0)
        page.screenshot(path=str(SHOTS / "05-refusal.png"), full_page=True)

        # ---- bilingual input: English question, Hindi answer ----
        # The gate was Devanagari-blind: _tokens() matched only Devanagari, so
        # every English question yielded zero tokens and was refused as
        # `too_short`, including good ones.
        # Wait for NEW parts, not for ".part" to exist: an earlier answer in this
        # same thread already satisfies that selector, so the wait returned at
        # once and the assertion measured the confirmation card instead.
        parts_before = page.locator(".part").count()
        page.fill("#input", "how many grams are in one kilogram?")
        page.click("#send")
        page.wait_for_selector(".confirm >> nth=1", timeout=30_000)
        page.locator("[data-yes]").last.click()
        page.wait_for_function(
            "n => document.querySelectorAll('.part').length > n",
            arg=parts_before, timeout=120_000)
        page.wait_for_timeout(400)
        last = page.locator(".card").last.inner_text()
        deva = sum(1 for ch in last if "\u0900" <= ch <= "\u097f")
        check("English question is accepted, not refused as too short",
              page.locator(".part").count() > parts_before,
              f"parts {parts_before} -> {page.locator('.part').count()}")
        check("English question is answered in Hindi",
              deva > 40, f"{deva} Devanagari chars in the answer card")
        page.screenshot(path=str(SHOTS / "08-english.png"), full_page=True)

        # ---- the spoken-language selector ----
        check("spoken-language selector offered", page.locator("[data-lang]").count() == 2)
        page.locator('[data-lang="en"]').click()
        page.wait_for_timeout(200)
        check("selector switches to English",
              page.locator('[data-lang="en"]').get_attribute("aria-pressed") == "true"
              and page.get_attribute("#input", "placeholder") == "Type your question…",
              page.get_attribute("#input", "placeholder"))
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#dot.ready", timeout=240_000)
        check("selector choice survives a reload",
              page.locator('[data-lang="en"]').get_attribute("aria-pressed") == "true")
        page.locator('[data-lang="hi"]').click()

        # ---- dark mode must be legible, not inherited ----
        ctx2 = browser.new_context(viewport={"width": 430, "height": 900},
                                   color_scheme="dark", locale="hi-IN")
        p2 = ctx2.new_page()
        p2.goto(BASE, wait_until="domcontentloaded")
        bg = p2.evaluate("() => getComputedStyle(document.body).backgroundColor")
        check("dark mode paints its own background",
              bg not in ("rgba(0, 0, 0, 0)", "transparent"), bg)
        p2.screenshot(path=str(SHOTS / "06-dark.png"), full_page=True)
        ctx2.close()

        # ---------------- paths that had never been exercised ----------------
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"page2: {e}"))
        pg.goto(BASE, wait_until="domcontentloaded")
        pg.wait_for_selector("#dot.ready", timeout=240_000)

        # the parent says "no, I meant something else" — the question must come back
        pg.fill("#input", "भिन्न क्या होती है?")
        pg.click("#send")
        pg.wait_for_selector("[data-no]", timeout=30_000)
        pg.click("[data-no]")
        pg.wait_for_timeout(300)
        restored = pg.input_value("#input")
        check("FR-2 'no' restores the question for editing",
              restored == "भिन्न क्या होती है?", repr(restored))
        check("FR-2 'no' re-enables the composer",
              not pg.locator("#send").is_disabled())

        # A typed question the gate has already rejected must go STRAIGHT to the
        # refusal. It used to ask the parent to confirm it first and then refuse
        # with the same sentence, so the same message was rendered three times.
        pg.fill("#input", "इसका जवाब क्या है?")
        pg.click("#send")
        # .card.asking, not .card.refusal: "ask me again more clearly" is a clarify
        # outcome, and painting it in warning red overstates what happened.
        pg.wait_for_selector(".card.asking", timeout=30_000)
        why = pg.inner_text(".card.asking")
        check("a clarify outcome is not styled as a refusal",
              pg.locator(".card.refusal").count() == 0,
              f"refusal cards={pg.locator('.card.refusal').count()}")
        check("typed vague question skips the pointless confirm turn",
              pg.locator(".clarify").count() == 0
              and pg.locator("[data-yes]").count() == 0,
              f"clarify={pg.locator('.clarify').count()} confirm={pg.locator('[data-yes]').count()}")
        # the server named the cause, so the interface must not add a second
        # explanation saying the same thing underneath it
        check("cause is explained exactly once",
              pg.locator(".card.asking .refusal-why").count() == 0,
              f"why lines={pg.locator('.card.asking .refusal-why').count()}")
        check("vague question answered in Hindi, no reason code",
              any("\u0900" <= ch <= "\u097f" for ch in why) and "_" not in why,
              why.replace("\n", " ")[:60])

        # NO raw reason code may ever reach the parent's view
        visible = pg.inner_text("body")
        leaked = [c for c in ("no_maths_topic", "beyond_class5", "out_of_syllabus",
                              "below_similarity", "passage_does_not_cover",
                              "answer_failed_contract", "query_pre_check")
                  if c in visible]
        check("no internal reason codes visible to the parent", not leaked, str(leaked))

        # The refusal card above already carries a .dev panel. This matters: .dev is
        # only rendered on answer and refusal cards, and is_visible() on a locator
        # matching nothing is False — so without a card present the "hidden by
        # default" check below would pass while asserting nothing.
        # state="attached", not the default "visible": the panel is hidden BY DESIGN
        # until the toggle is pressed, so waiting for visibility waits forever.
        pg.wait_for_selector(".dev", state="attached", timeout=120_000)

        # the machinery is available on demand, for whoever is evaluating this
        check("HOW IT WORKS panel hidden by default",
              pg.locator(".dev").first.count() > 0
              and not pg.locator(".dev").first.is_visible())
        pg.click("#devtoggle")
        pg.wait_for_timeout(200)
        check("HOW IT WORKS panel reveals the internals",
              pg.locator(".dev").first.is_visible()
              and "gate" in pg.locator(".dev").first.inner_text())
        pg.screenshot(path=str(SHOTS / "07-devmode.png"), full_page=True)
        pg.click("#devtoggle")

        # ---- the ABOUT panel: the demo's only English surface ----
        #
        # A stranger opening this link is more likely to be evaluating the product
        # than using it, and everything else on screen is Hindi. If this panel
        # breaks, the demo silently stops explaining itself.
        check("about panel closed by default",
              not pg.locator("#about").is_visible())
        pg.click("#aboutbtn")
        pg.wait_for_timeout(400)
        about = pg.locator("#about")
        check("about panel opens", about.is_visible())

        # This is a REGRESSION TEST for a real bug, not a formality. The body state
        # class was `about`, which also matched the panel's own `.about` rule — so
        # <body class="about"> itself became position:fixed and translateX(101%),
        # sliding the entire document off-screen. Every DOM query still reported
        # the content present, visible and correctly coloured; only a screenshot
        # showed a blank white page. Assert the document's own geometry.
        geom = pg.evaluate("""() => ({
            overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            headerX: Math.round(document.querySelector('header').getBoundingClientRect().x),
            panelX: Math.round(document.getElementById('about').getBoundingClientRect().x),
        })""")
        check("opening about does not move the document",
              geom["overflow"] <= 1 and geom["headerX"] < 40 and geom["panelX"] < 40,
              str(geom))

        about_text = about.inner_text()
        check("about panel states what was measured",
              all(k in about_text for k in ("87%", "1.1%", "93%")),
              about_text[:60].replace("\n", " "))
        check("about panel is honest about what is not done",
              "not done" in about_text.lower() and "untested" in about_text.lower())
        pg.screenshot(path=str(SHOTS / "08-about.png"))
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        check("about panel closes on Escape", not about.is_visible())

        # ---- theme control ----
        # "system" must stay the default: a parent whose phone is already in dark
        # mode has told us, and an explicit choice only exists once they overrule it.
        check("theme follows the system until told otherwise",
              pg.evaluate("() => document.documentElement.getAttribute('data-theme')") is None)
        pg.click("#themebtn")
        pg.wait_for_timeout(250)
        check("theme toggle takes effect",
              pg.evaluate("() => document.documentElement.getAttribute('data-theme')")
              in ("dark", "light"))

        # an over-long question is refused in Hindi before any request is made
        pg.fill("#input", "क " * 300)
        pg.click("#send")
        pg.wait_for_timeout(400)
        check("over-long question refused in Hindi",
              "लंबा" in pg.inner_text("#hint"), pg.inner_text("#hint"))

        # empty send must not create a turn
        before = pg.locator(".from-parent").count()
        pg.fill("#input", "   ")
        pg.click("#send")
        pg.wait_for_timeout(300)
        check("empty question creates no turn",
              pg.locator(".from-parent").count() == before)

        # Double-send must not fire twice. The composer disables while busy, but
        # asserting THAT directly is a race: it was a fixed 250 ms sleep, and once
        # the query embedding moved to Workers AI the whole turn finished inside
        # that window, so a correctly re-enabled composer read as a failure.
        #
        # Assert the requirement instead of the mechanism — clicking send twice
        # must produce exactly one new turn — which is what the comment always
        # said this test was for, and which does not depend on how fast the
        # backend happens to be.
        before_turns = pg.locator(".from-parent").count()
        pg.fill("#input", "1 मिनट में कितने सेकंड होते हैं?")
        pg.click("#send")
        was_disabled = pg.locator("#send").is_disabled()   # sampled with no sleep
        pg.click("#send", force=True)                      # the second, ignored click
        pg.wait_for_selector("[data-yes]", timeout=30_000)
        check("a double send creates one turn, not two",
              pg.locator(".from-parent").count() == before_turns + 1,
              f"{pg.locator('.from-parent').count() - before_turns} new turns")
        check("composer guards against re-entry while busy", was_disabled)
        pg.locator("[data-yes]").last.click()
        pg.wait_for_selector(".card .part", timeout=120_000)
        pg.wait_for_timeout(500)
        check("composer re-enabled after answering",
              not pg.locator("#send").is_disabled())
        pg.close()

        check("no JavaScript errors", not errors, "; ".join(errors[:3]))

        browser.close()

    print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  failures: " + ", ".join(FAIL))
    print(f"  screenshots -> {SHOTS}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
