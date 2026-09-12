# Deploying Homework Saathi

The goal is a link a stranger can open. That is not a nice-to-have: the WhatsApp
Cloud API test number can only message pre-approved recipients, so until a
business account exists, **the web chat is the only surface that can actually be
demonstrated to anyone.**

---

## Honest status of this file

| Thing | Verified? |
|---|---|
| Env-var configuration (`PORT`, `HOST`, `SAATHI_BACKEND`, `SAATHI_CACHE`) | **Yes** — the server was run exactly as the container runs it |
| `/api/health` and `/api/status` | **Yes** |
| Page images fetched from ncert.nic.in with no local book | **Yes** — 80 KB in 1.8 s cold, then cached |
| `preflight.py` | **Yes** — 17 checks, 0 failures on this laptop |
| **The Docker image building** | **No.** Docker is not installed on this machine. The Dockerfile is written from the constraints below but has never been built. Expect to fix one or two lines on the first build. |

Everything except the image build is tested. The image build is the one step
you should expect to iterate on.

---

## Why Hugging Face Spaces

BGE-M3 needs about **1.9 GB to load and peaks near 3.3 GB while encoding**. That
single number eliminates almost every free tier:

| Host | Free memory | Verdict |
|---|---|---|
| **HF Spaces (Docker)** | **2 vCPU / 16 GB** | **works** |
| Render free | 512 MB | cannot load the model |
| Fly.io free | 256 MB – 1 GB | cannot load the model |
| Vercel / Netlify | serverless, no persistent RAM | wrong shape entirely |

Nothing in the Dockerfile is HF-specific except the default port, so any
container host with ~4 GB of RAM runs the same image.

---

## Steps

### 0. Build the deployable folder

```bash
python scripts/make_deploy.py --git
```

This assembles `deploy/` — 18 files, 4.8 MB — containing only what serves a
question: the 9 runtime modules, the 4 derived artefacts, the interface, a
Dockerfile and a Space README. It carries **no textbook, no `.env`, and none of
the ingest or eval toolchain**. `--git` initialises a git repo inside it so you
can push it straight to a Space; rebuilding later keeps that history.

`scripts/make_deploy.py` is the single source of truth for what a deployment
contains — edit the project's own files, then regenerate. Never edit inside
`deploy/`, because the next rebuild overwrites it.

Verify it before pushing:

```bash
python scripts/make_deploy.py --check    # is the folder self-contained?
cd deploy && python scripts/preflight.py # 18 checks, run from inside the folder
```

### 1. Create the Space

At <https://huggingface.co/new-space>: choose **Docker → Blank**, hardware
**CPU basic (free)**, and set it **Public** so the link works for anyone.

### 2. The Space README is already written

HF Spaces is configured by YAML frontmatter in the Space repo's `README.md`.
`make_deploy.py` writes that file, frontmatter and all, so there is nothing to do
here — it is `deploy/README.md`.

### 3. Add secrets

In **Settings → Variables and secrets**, add them as *secrets*, not variables:

| Secret | Needed? | Without it |
|---|---|---|
| `GROQ_API_KEY` | **yes** | nothing can be generated |
| `BHASHINI_USER_ID`, `BHASHINI_API_KEY` | optional | voice falls back to the browser speech API |
| `GEMINI_API_KEY` | **off by design** | picture-only questions are refused with a reason |

**One credential is all this needs.** Figure reading was deliberately switched off
(D18) after its key was exposed; the capability stays in the code and can be
re-enabled by setting the variable, but nothing depends on it.

**Rotate `GROQ_API_KEY` before deploying** — it has been pasted into a chat
transcript more than once: <https://console.groq.com/keys>. Deleting the old key
is what revokes it; creating a new one leaves the old one live.

### 4. Push

```bash
cd deploy
git remote add space https://huggingface.co/spaces/<you>/homework-saathi
git push space main
```

The first build takes roughly 10–15 minutes, most of it downloading torch and
baking BGE-M3 into the image. Baking it is deliberate: pulling 2.1 GB on boot
means the app is up but every question 503s for several minutes, which is the
worst state for a demo link to be in.

### 5. Verify the deployment rather than trusting it

```bash
python scripts/preflight.py --url https://<you>-homework-saathi.hf.space
```

This checks health, that models finished loading, that the backend is not the
stub, and that a real textbook page comes back inside the metered-data budget.

---

## What the container is doing differently

**It does not carry the textbook.** `ingest/raw`, `ingest/pages` and
`ingest/extracted` are excluded by `.dockerignore` because every page reads
"© NCERT / not to be republished". When a parent taps *पेज देखिए*,
`scripts/pagesource.py` fetches that chapter's PDF from `ncert.nic.in`, caches
it, and renders the single page asked for. The bytes come from NCERT's own
server; this app redistributes nothing. First hit per chapter ~2–4 s, then
~0.2 s.

**It installs a different dependency set.** `requirements-deploy.txt` is the four
packages `serve.py` actually imports at runtime. `requirements.txt` is the ingest
and eval environment — pypdf, pdfminer, rapidfuzz and playwright all belong to
building and testing the corpus, not to answering a question.

**It installs CPU-only torch.** The default wheel bundles the CUDA runtime:
about 2 GB of GPU libraries on a box with no GPU.

---

## The free tier is the capacity limit

Groq's free tier is **8,000 tokens/minute and 200,000 tokens/day**, and an answer
costs ~2,200 tokens. That is **roughly 90 answers per day, per organisation** —
rotating the key does not reset it. `scripts/cost_model.py` prices this out.

For a portfolio demo that is fine. For a pilot it is the first thing to fix, and
the fix is a second free provider rather than a paid tier (§14 requires vetting
the data policy of any new one first).

---

## Running it locally

```bash
python scripts/serve.py                      # real answers, spends Groq tokens
python scripts/serve.py --backend stub       # canned answers, spends nothing
python scripts/preflight.py                  # is this box able to serve?
python scripts/test_ui.py                    # 51 browser assertions (needs stub)
```
