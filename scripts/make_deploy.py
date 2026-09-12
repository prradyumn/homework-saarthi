"""Assemble `deploy/` — a self-contained folder ready to push to a host.

Why a generator and not a checked-in folder: the deploy folder needs its own git
history (you push it to a Hugging Face Space as that Space's repo), and it would
otherwise hold a second copy of `answer.py`, `serve.py` and the rest. Two copies
of the same module in one repo drift, and the copy that drifts is always the one
nobody runs locally. So the folder is generated, gitignored, and this file is the
single source of truth for what a deployment contains.

    python scripts/make_deploy.py              # build ./deploy
    python scripts/make_deploy.py --git        # ...and init a git repo inside it
    python scripts/make_deploy.py --check      # verify an existing ./deploy

What deliberately does NOT go in:

  * **the textbook** — `ingest/raw`, `ingest/pages`, `ingest/extracted` and the
    on-demand `ingest/pdf_cache`. Every page reads "© NCERT / not to be
    republished" (HANDOFF §2). `pagesource.py` fetches the one page a parent asks
    for from ncert.nic.in at request time instead.
  * **`.env`** — secrets arrive as the host's environment variables. A key baked
    into an image layer is a key you cannot rotate without a rebuild.
  * **the ingest and eval toolchain** — pypdf, pdfminer, rapidfuzz, playwright,
    and the 25-odd scripts that build and measure the corpus. They are how the
    artefacts were made, not what serves them.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "deploy"

# Exactly what `serve.py` reaches at runtime, traced through the import graph:
#   serve -> pagesource, answer, query_gate, bhashini, vision
#   answer -> retrieval, query_gate, answer_contract, vision
# Everything else under scripts/ builds or measures the corpus and has no place
# on a public box.
RUNTIME_SCRIPTS = [
    "serve.py",          # the HTTP surface
    "answer.py",         # gate -> retrieve -> generate -> validate
    "answer_contract.py",  # the §10 four-part validator
    "query_gate.py",     # deterministic pre-checks, before any model call
    "retrieval.py",      # hybrid dense + lexical, parent->textbook vocabulary
    "pagesource.py",     # FR-10 page images, incl. the fetch-from-NCERT route
    "vision.py",         # reads figures (optional; degrades to refusing)
    "bhashini.py",       # ASR/TTS (optional; degrades to browser speech)
    "preflight.py",      # so a deployed box can check itself
]

# The derived artefacts. These are transformations for retrieval, not a readable
# substitute for the book.
RUNTIME_INGEST = [
    "chunks.json",         # 248 Class 5 concept-unit chunks
    "decoy_chunks.json",   # 516 out-of-syllabus chunks — the syllabus boundary
    "index_combined.npz",  # BGE-M3 embeddings for both
    "manifest.json",       # printed page -> chapter PDF map (asserted in D0.2)
]

SPACE_README = """---
title: Homework Saathi
emoji: 📕
colorFrom: yellow
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: A Hindi homework helpline for parents, built to refuse rather than guess.
---

# होमवर्क साथी — Homework Saathi

A Hindi, voice-first homework helpline for **parents** of Class 5
government-school children in India — deliberately not for the children. It
answers from the child's actual NCERT textbook (गणित मेला, NCF-2023), and its
output is not an answer but a sentence the parent can say out loud.

> हम बच्चों के ट्यूटर नहीं हैं — हम माता-पिता के कोच हैं।
> *We are not a tutor for children. We are a coach for parents.*

## The hard part is not generating — it is refusing

Many of these parents left school before the class their child is now in. They
cannot check a maths answer, which is exactly why they are asking. A confidently
wrong answer that a parent then teaches their child is far worse than "I don't
know". So the system is built around a refusal gate calibrated on a 150-question
labelled set, and it declines rather than guesses.

Open the app and try the dashed example question — it is Class 10 algebra, and it
gets refused with a reason, a way forward, and no guess.

## Measured

| | |
|---|---|
| **87%** | of answerable questions get answered (n=150) |
| **1.1%** | wrong-answer rate — 1 of 88 answered |
| **13%** | of legitimate questions refused (guardrail: 25%) |
| **93%** | retrieval returns the right passage (chunk-level, top-3, n=30) |
| **0.7%** | character error rate reading the textbook |
| **83%** | answers meeting the four-part contract (n=30) |

Press **ABOUT** in the app for the full picture, including what is *not* done.

## Configuration

| Secret | Needed? | Without it |
|---|---|---|
| `GROQ_API_KEY` | **yes** | nothing can be generated |
| `BHASHINI_USER_ID` + `BHASHINI_API_KEY` | optional | voice falls back to the browser speech API |
| `GEMINI_API_KEY` | **off by design** | questions answerable only from a picture are refused with a reason, rather than read |

Set these as **Space secrets**, never in a file.

Only `GROQ_API_KEY` is required. The default deployment runs with generation only:
figure reading is built and tested (`scripts/vision.py`) but ships switched off, so
the box needs exactly one credential and chart questions get an honest refusal.

## The textbook is not in this image

Every page of the source carries "© NCERT / not to be republished", so no page
images ship here. When a parent taps *पेज देखिए*, `scripts/pagesource.py` fetches
that chapter's PDF from `ncert.nic.in`, caches it, and renders the single page
asked for. The bytes come from NCERT's own server; this app redistributes nothing.

---

Built by Pradyumn Awasthi. Generated from the project repo by
`scripts/make_deploy.py` — edit the source there, not the copies here.
"""

SPACE_GITIGNORE = """# secrets arrive as Space secrets, never as a file
.env

# chapter PDFs fetched on demand — same book, same licence, not ours to ship
ingest/pdf_cache/

__pycache__/
*.pyc
"""


def _payload(root: pathlib.Path):
    """The files that actually get deployed — .git is history, not payload."""
    return [p for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts]


def build(init_git: bool) -> int:
    # Rebuilding must not destroy the folder's own git history. Once you have
    # pushed this to a Space, deploy/.git holds that remote and every previous
    # deployment — wiping it on the next rebuild would silently orphan the Space.
    stash = None
    if (OUT / ".git").exists():
        stash = ROOT / ".deploy-git-stash"
        if stash.exists():
            shutil.rmtree(stash)
        shutil.move(str(OUT / ".git"), str(stash))
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "scripts").mkdir(parents=True)
    if stash is not None:
        shutil.move(str(stash), str(OUT / ".git"))
        print("  kept the existing git history in deploy/.git")
    (OUT / "ingest").mkdir(parents=True)
    (OUT / "web").mkdir(parents=True)

    written = []

    for name in RUNTIME_SCRIPTS:
        src = ROOT / "scripts" / name
        if not src.exists():
            print(f"  MISSING {src}", file=sys.stderr)
            return 1
        shutil.copy2(src, OUT / "scripts" / name)
        written.append(f"scripts/{name}")

    for name in RUNTIME_INGEST:
        src = ROOT / "ingest" / name
        if not src.exists():
            print(f"  MISSING {src}", file=sys.stderr)
            return 1
        shutil.copy2(src, OUT / "ingest" / name)
        written.append(f"ingest/{name}")

    shutil.copy2(ROOT / "web" / "index.html", OUT / "web" / "index.html")
    written.append("web/index.html")

    shutil.copy2(ROOT / "Dockerfile", OUT / "Dockerfile")
    # Inside the deploy folder there is only one requirements file, so it takes
    # the plain name the Dockerfile and every host's autodetection expect.
    shutil.copy2(ROOT / "requirements-deploy.txt", OUT / "requirements.txt")
    docker = (OUT / "Dockerfile").read_text().replace(
        "requirements-deploy.txt", "requirements.txt")
    (OUT / "Dockerfile").write_text(docker)
    written += ["Dockerfile", "requirements.txt"]

    (OUT / "README.md").write_text(SPACE_README)
    (OUT / ".gitignore").write_text(SPACE_GITIGNORE)
    written += ["README.md", ".gitignore"]

    # The book must not be here. Assert it rather than trust the copy list.
    forbidden = [p for p in _payload(OUT)
                 if p.suffix.lower() in {".pdf", ".png", ".jpg"} or p.name == ".env"]
    if forbidden:
        print("  REFUSING: the book or a secret reached the deploy folder:",
              file=sys.stderr)
        for f in forbidden:
            print(f"    {f.relative_to(OUT)}", file=sys.stderr)
        return 1

    size = sum(p.stat().st_size for p in _payload(OUT))
    print(f"\n  deploy/  —  {len(written)} files, {size / 1024 / 1024:.1f} MB\n")
    for w in sorted(written):
        print(f"    {w}")

    if init_git:
        if (OUT / ".git").exists():
            print("\n  git repo already present")
        else:
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=OUT, check=True)
            subprocess.run(["git", "add", "-A"], cwd=OUT, check=True)
            subprocess.run(["git", "-c", "user.email=pradyumn@convegenius.ai",
                            "-c", "user.name=Pradyumn Awasthi",
                            "commit", "-q", "-m",
                            "Homework Saathi — deployable prototype"],
                           cwd=OUT, check=True)
            print("\n  git repo initialised with one commit")

    print(f"""
  next:
    cd deploy
    git remote add space https://huggingface.co/spaces/<you>/homework-saathi
    git push space main

  then set GROQ_API_KEY as a Space secret and check the live box with:
    python scripts/preflight.py --url https://<you>-homework-saathi.hf.space
""")
    return 0


def check() -> int:
    """Does the generated folder hold together on its own?"""
    if not OUT.exists():
        print("  no deploy/ — run without --check first", file=sys.stderr)
        return 1

    bad = 0
    for name in RUNTIME_SCRIPTS + ["Dockerfile", "requirements.txt", "README.md"]:
        rel = f"scripts/{name}" if name.endswith(".py") else name
        if not (OUT / rel).exists():
            print(f"  FAIL missing {rel}")
            bad += 1

    # Every module the copied scripts import from each other must be present, or
    # the box dies on its first request instead of at build time.
    import re

    available = {p.stem for p in (OUT / "scripts").glob("*.py")}
    for p in (OUT / "scripts").glob("*.py"):
        for m in re.findall(r"^\s*(?:from|import) ([a-z_][a-z0-9_]*)",
                            p.read_text(), re.M):
            if m in {q.stem for q in (ROOT / "scripts").glob("*.py")} \
                    and m not in available:
                print(f"  FAIL {p.name} imports '{m}', which was not copied")
                bad += 1

    forbidden = [p for p in _payload(OUT)
                 if p.suffix.lower() in {".pdf", ".png", ".jpg"}]
    if forbidden:
        print(f"  FAIL the book reached deploy/: {len(forbidden)} image/PDF files")
        bad += 1
    if (OUT / ".env").exists():
        print("  FAIL .env is in deploy/")
        bad += 1

    print(f"\n  {'ok — deploy/ is self-contained' if not bad else f'{bad} problems'}\n")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--git", action="store_true",
                    help="init a git repo in deploy/ and make the first commit")
    ap.add_argument("--check", action="store_true",
                    help="verify an existing deploy/ instead of rebuilding")
    args = ap.parse_args()
    return check() if args.check else build(args.git)


if __name__ == "__main__":
    sys.exit(main())
