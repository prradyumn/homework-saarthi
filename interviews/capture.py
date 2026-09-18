"""Record one parent interview, straight after it happens.

Memory decays fast and in a flattering direction — you will remember the parent
agreeing with you. This walks `interviews/GUIDE.md` in order and writes one JSON
file per session, so the analysis runs on what was said rather than on what was
recalled.

    python interviews/capture.py

Nothing identifying is stored. Participants get a sequence number, not a name;
§14 makes that a data-governance decision rather than an oversight, and a
transcript of a stranger's conversation about their child's schooling is not
something a portfolio project should be holding.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SESSIONS = HERE / "sessions"

SCREEN = [
    ("class5", "Child is currently in Class 5?"),
    ("govt_school", "Child attends a government school?"),
    ("asked_recently", "Has the child asked them for homework help in the last month?"),
    ("hindi_home", "Hindi is the main language at home?"),
]


def ask(prompt: str) -> str:
    try:
        return input(f"  {prompt}\n  > ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  aborted — nothing written")
        sys.exit(1)


def yesno(prompt: str) -> bool:
    while True:
        v = ask(f"{prompt} [y/n]").lower()
        if v in ("y", "yes", "n", "no"):
            return v.startswith("y")
        print("    answer y or n")


def pick(prompt: str, options: list[str]) -> str:
    opts = "/".join(options)
    while True:
        v = ask(f"{prompt} [{opts}]").upper() if len(options[0]) == 1 else ask(
            f"{prompt} [{opts}]").lower()
        if v in [o.upper() if len(o) == 1 else o.lower() for o in options]:
            return v
        print(f"    answer one of: {opts}")


def main() -> int:
    SESSIONS.mkdir(parents=True, exist_ok=True)
    n = len(list(SESSIONS.glob("p*.json"))) + 1
    print(f"\n  Interview {n}. Ctrl-C aborts without writing.\n")

    print("  --- screening (all four must be yes) ---")
    screen = {k: yesno(q) for k, q in SCREEN}
    if not all(screen.values()):
        failed = [k for k, v in screen.items() if not v]
        print(f"\n  NOT ELIGIBLE ({', '.join(failed)}). Do not run the interview.")
        print("  A wrong participant is worse than a missing one — their answers")
        print("  get averaged in anyway. Nothing written.\n")
        return 2

    print("\n  --- 2. warm-up: what actually happens ---")
    warmup = {
        "last_time": ask("Last time the child asked for help — what happened?"),
        "what_they_did": ask("What did the parent do?"),
        "when_stuck": ask("What do they do when they don't know the answer?"),
        "their_words": ask("Their EXACT words for being stuck (feeds PARENT_TO_BOOK):"),
    }

    print("\n  --- 3. card test (hand the cards over; do not explain first) ---")
    card_top = pick("Which card was on top?", ["A", "B"])
    card = {
        "top_card": card_top,
        "q1_more_useful": pick("Q1 — which is more useful to them?", ["A", "B"]),
        "q2_why": ask("Q2 — why?"),
        "q3_only_one": pick("Q3 — if only ONE every night, which? (DECIDES)", ["A", "B"]),
    }

    print("\n  --- 4. Q11: does a refusal lose them? ---")
    q11 = {
        "a_reaction": ask("4a — after the live refusal, what did they say? (verbatim)"),
        "a_understood": yesno("4a — did they understand it DECLINED (vs think it broke)?"),
        "a_next": ask("4a — what will they do next? (verbatim)"),
        "b_neighbour_thinks": ask("4b — what would the neighbour think?"),
        "b_neighbour_returns": yesno("4b — would the neighbour use it again?"),
        "c_preference": pick(
            "4c — honest-but-sometimes-declines, or always-answers-sometimes-wrong? (DECIDES)",
            ["honest", "always"]),
        "c_why": ask("4c — why?"),
    }

    print("\n  --- 5. close ---")
    close = {
        "would_miss": ask("Would they miss it if it vanished? Why?"),
        "would_refer": ask("What would make them tell someone else about it?"),
        "unprompted": ask("Anything they raised that you didn't ask about?"),
    }

    rec = {
        "participant": f"p{n:02d}",
        "date": dt.date.today().isoformat(),
        "screening": screen,
        "warmup": warmup,
        "card_test": card,
        "q11": q11,
        "close": close,
    }
    out = SESSIONS / f"p{n:02d}.json"
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  written -> {out.relative_to(HERE.parent)}")
    print(f"  {n} of 8 captured. Run: python interviews/analyse.py\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
