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
| Slim runtime with no torch | **Yes** — retrieval re-measured at 93%, unchanged by the refactor |
| Clean failure when the embedder has no credentials | **Yes** — raises `NotConfigured` with the signup steps, rather than crashing |
| **Cloudflare returning vectors compatible with the shipped index** | **Not yet.** Needs an account. `python scripts/embedder.py --compare` settles it in one command and MUST pass before this ships — see below. |
| **The Docker image building** | **No.** Docker is not installed on this machine. The Dockerfile has never been built. Expect to fix a line or two on the first build. |

Everything except the image build is tested. The image build is the one step
you should expect to iterate on.

---

## Why not Hugging Face, and what changed

The original plan was a Docker Space, because BGE-M3 needs ~1.9 GB to load and
peaks near 3.3 GB while encoding — and HF Spaces free was the only free tier with
that much memory. **In July 2026 Hugging Face moved both the Docker and Gradio
SDKs behind PRO for personal accounts.** Only Static Spaces remain free, and a
static Space cannot run Python.

Rather than pay, the model came out of the container (D19). The corpus index was
always precomputed; the only thing needing a model at request time is the *query*
vector, and that now goes to **Cloudflare Workers AI running `@cf/baai/bge-m3` —
the same model the index was built with**, which is what keeps the shipped vectors
and every measured number valid.

| | before | after |
|---|---|---|
| image | ~4 GB (torch + BGE-M3) | **~300 MB** |
| RAM needed | ~4 GB | **under 512 MB** |
| free hosts that fit | HF Spaces (now PRO) | Render, Koyeb, Fly, a VM — no card |
| build time | 10–15 min | **~2 min** |

Local development and **every eval still run BGE-M3 on the machine**, offline and
with no credential. `SAATHI_EMBED` picks the backend and defaults to local, so the
reproducible numbers stay reproducible.

### The free ceilings, and which one binds

| | free allowance | what that is |
|---|---|---|
| Cloudflare Workers AI | 10,000 neurons/day | bge-m3 costs 1,075 neurons per M input tokens; a query is ~30 tokens → **~300,000 queries/day** |
| Groq | 200,000 tokens/day | ~2,200 per answer → **~90 answers/day** |
| Render free | 750 instance-hours/month | one service running continuously fits |

**Groq binds, by a factor of about 3,000.** Embedding will never be the limit.

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

### 1. Create the service

At <https://render.com> → **New → Web Service** → connect the `deploy/` repo (or
push it to GitHub first). Choose **Docker** as the runtime and the **Free** plan.

Free instances spin down after 15 minutes idle and take about a minute to wake, so
the first click on a cold link waits. That is the price of not paying; if it
matters for a specific demo, open the link yourself a minute beforehand.

### 2. Nothing to configure

The `Dockerfile` sets `PORT`, `HOST=0.0.0.0` and `SAATHI_EMBED=cloudflare`, and
Render reads the port from the environment. `deploy/README.md` carries HF Spaces
frontmatter too, which is harmless elsewhere and keeps that door open if you ever
take a PRO plan.

### 3. Add secrets

In **Settings → Variables and secrets**, add them as *secrets*, not variables:

| Secret | Needed? | Without it |
|---|---|---|
| `GROQ_API_KEY` | **yes** | nothing can be generated |
| `CF_ACCOUNT_ID`, `CF_API_TOKEN` | **yes** | no query can be embedded, so nothing can be retrieved |
| `BHASHINI_USER_ID`, `BHASHINI_API_KEY` | optional | voice falls back to the browser speech API |
| `GEMINI_API_KEY` | **off by design** | picture-only questions are refused with a reason |

**Two credentials, both free, neither needing a card.**

Cloudflare: sign in at <https://dash.cloudflare.com>, take the **Account ID** from
the right-hand sidebar, then **My Profile → API Tokens → Create Token → Custom**,
with the single permission **Account · Workers AI · Read**. Nothing else.

Figure reading was deliberately switched off (D18) after its key was exposed; the
capability stays in the code and can be re-enabled by setting the variable, but
nothing depends on it.

**Rotate `GROQ_API_KEY` before deploying** — it has been pasted into a chat
transcript more than once: <https://console.groq.com/keys>. Deleting the old key
is what revokes it; creating a new one leaves the old one live.

### 4. Push

Nothing to push — the project is already on GitHub at
`prradyumn/homework-saarthi`, and `render.yaml` at the repo root tells Render how
to build it. Point a **Blueprint Instance** at the repo and it configures itself,
prompting only for the three secrets.

`deploy/` is a minimal proof that the runtime is self-contained; Render does not
need it. The build is ~2 minutes now that there is no model to download.

### 5. Verify the deployment rather than trusting it

```bash
python scripts/preflight.py --url https://<your-service>.onrender.com
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

## Before shipping: prove the embedder is the same model

Two services running "the same model" can still differ in pooling or
normalisation. A query vector that is subtly wrong **does not throw** — it quietly
returns the wrong passage, and every number in the case study stops being true
without anything failing.

```bash
CF_ACCOUNT_ID=... CF_API_TOKEN=... python scripts/embedder.py --compare
```

It embeds the same five queries locally and on Workers AI, reports cosine
agreement per query, and checks that both rank the real corpus identically in the
top 5. **Below 0.999 means a different model — do not ship it**; re-embed the
corpus against whatever is actually being served, and re-run the evals.

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
