# COWORK — finish the deployment

A runbook for an agent picking this up. It covers exactly one job: **turn the
working local prototype into a public URL a stranger can open**, and put that URL
into the showcase.

Everything else about this project is already done and is not your task. Read
`HANDOFF.md` for the whole picture and `eval/DECISIONS.md` for why things are the
way they are. Do not re-derive either.

Project root (note the space in the path — quote it everywhere):

```
/Users/pradyumnawasthi/homework saarthi
```

Python is `./.venv/bin/python`. There is no `timeout` command on this machine.

---

## Where things stand

| | |
|---|---|
| App | Works end to end, locally and from `deploy/`. 51/51 browser assertions pass. |
| `deploy/` | Generated, self-contained, 19 files, ~4.9 MB, its own git history, one commit ahead. |
| Generation | Groq, key in `.env`, **rotated and verified** (old key returns 401). |
| Query embedding | Refactored out of the container to Cloudflare Workers AI. **Unverified.** |
| Credentials | `GROQ_API_KEY` ✅ · `CF_API_TOKEN` ✅ · **`CF_ACCOUNT_ID` ❌ missing** |
| Host | Render free tier. Not yet created. |
| Showcase | Published, live, has no demo link yet. |

The two things standing between here and done are **the embedder compatibility
gate** and **a Render service**.

---

## Non-negotiables

Break any of these and the work is worse than not doing it.

1. **Never commit the textbook.** `ingest/raw`, `ingest/pages`, `ingest/extracted`,
   `ingest/pdf_cache` are © NCERT, "not to be republished." They are gitignored.
   If `git status` ever shows a `.pdf` or a page `.png`, stop and remove it.
2. **Never commit `.env`.** Secrets reach the host as environment variables.
3. **Never paste a credential into chat.** Four have leaked that way already. Ask
   the human to put it in `.env` themselves, or into the host's secrets UI.
4. **Never put a key in `argv`.** It is readable by every process and lands in
   shell history. Use a header file — see `embedder.py::_embed_cloudflare`.
5. **Do not edit anything inside `deploy/`.** It is generated. Edit the real file
   and re-run `make_deploy.py`, or your change disappears on the next build.
6. **Do not assert a free tier from memory.** That mistake already cost this
   project a whole deployment plan: Hugging Face moved Docker Spaces behind PRO
   in July 2026 and the guide recommending them was wrong the day it was written.
   Check the provider's live page.
7. **Never report a step as done without running it.** If you could not run it,
   say so in exactly those words.

---

## Step 1 — Get the Cloudflare Account ID

`CF_API_TOKEN` is already in `.env` and verified active. It is scoped to Workers
AI only, so it **cannot** enumerate accounts — `/client/v4/accounts` returns an
empty list. The Account ID has to come from the human.

It is **not a secret** (it appears in every API URL), so it is safe for them to
paste in chat.

Ask them for it like this:

> Open <https://dash.cloudflare.com>. The Account ID is in the address bar —
> `dash.cloudflare.com/<THIS_PART>/...` — or under **Workers & Pages → Overview**
> in the right sidebar, with a copy button.

Then append it:

```bash
cd "/Users/pradyumnawasthi/homework saarthi"
printf 'CF_ACCOUNT_ID=<the-id>\n' >> .env
chmod 600 .env
```

Confirm both are present without printing their values:

```bash
grep -oE '^[A-Z_]+=' .env | tr -d '='
# expect: GROQ_API_KEY  CF_API_TOKEN  CF_ACCOUNT_ID
```

---

## Step 2 — THE GATE: prove Cloudflare serves the same model

**This is the most important step in the runbook. Do not skip it, and do not
proceed on a fail.**

The corpus index (`ingest/index_combined.npz`) was embedded once with BGE-M3 and
ships precomputed. Only the query is embedded at request time. If Cloudflare's
`@cf/baai/bge-m3` differs from local BGE-M3 in pooling or normalisation, the query
vector is subtly wrong — and **a subtly wrong vector does not throw.** It silently
returns the wrong passage. Every test still passes. Every number in the showcase
quietly stops being true.

```bash
cd "/Users/pradyumnawasthi/homework saarthi"
./.venv/bin/python scripts/embedder.py --compare
```

It embeds five queries both ways and reports per-query cosine agreement, plus
whether both rank the real corpus identically in the top 5.

**Pass:** worst cosine ≥ 0.999 → the index stays valid, continue to Step 3.

**Fail:** worst cosine < 0.999 → **STOP.** Do not deploy. Report the actual
numbers to the human and give them the options:
- re-embed the whole corpus against whatever Cloudflare actually serves, then
  re-run `calibrate_refusal.py` and `eval_retrieval.py` and update every figure in
  `writeup/showcase.html`, `HANDOFF.md` and `deploy/README.md`; or
- find a different provider serving true BGE-M3; or
- pay for a host with enough RAM to run the model locally (see D19).

Do not pick for them. This changes the project's headline numbers.

**Also possible:** the call errors. `NotConfigured` means credentials are missing
(back to Step 1). An HTTP error from Workers AI usually means the token lacks the
**Account · Workers AI · Read** permission — ask the human to check the token's
scope, do not work around it.

---

## Step 3 — Rebuild and re-verify `deploy/`

```bash
cd "/Users/pradyumnawasthi/homework saarthi"
./.venv/bin/python scripts/make_deploy.py          # keeps deploy/.git
./.venv/bin/python scripts/make_deploy.py --check  # expect: self-contained
```

Then run the app's own preflight from **inside** the folder, with the slim
backend, exactly as the host will:

```bash
cd "/Users/pradyumnawasthi/homework saarthi/deploy"
SAATHI_EMBED=cloudflare SAATHI_CACHE=/tmp/saathi_cache \
CF_ACCOUNT_ID="$(grep '^CF_ACCOUNT_ID' ../.env | cut -d= -f2)" \
CF_API_TOKEN="$(grep '^CF_API_TOKEN' ../.env | cut -d= -f2)" \
GROQ_API_KEY="$(grep '^GROQ_API_KEY' ../.env | cut -d= -f2)" \
../.venv/bin/python scripts/preflight.py
```

`SAATHI_CACHE` matters: without it the fetched chapter PDFs land in
`deploy/ingest/pdf_cache/`, putting copyrighted material inside the folder you are
about to push. It is gitignored there, but do not rely on that — point the cache
outside.

**Pass criteria:** `0 failures`. Two warnings are expected and correct — figure
reading (off by design, D18) and Bhashini voice (no credentials). Warnings are
degraded-but-honest paths, not blockers.

If the four embedder checks fail (`embedder configured`, `a query embeds`,
`retrieval returns hits`, `query embedding`), they are all one root cause:
`CF_ACCOUNT_ID` is missing. Go back to Step 1.

**Do not pipe this through `tail`** while waiting — `tail` buffers until the
command exits, so a slow run looks like a hang. Redirect to a file and read it.

Commit inside the folder so it is push-ready:

```bash
cd "/Users/pradyumnawasthi/homework saarthi/deploy"
git add -A
git -c user.email=pradyumn@convegenius.ai -c user.name="Pradyumn Awasthi" \
    commit -m "Homework Saathi — deployable prototype"
```

---

## Step 4 — Push `deploy/` to GitHub

Render deploys from a git repo. `deploy/` already **is** one, with its own history
that survives rebuilds.

The human must create the GitHub repo (it needs their account). Ask for the URL,
then:

```bash
cd "/Users/pradyumnawasthi/homework saarthi/deploy"
git remote add origin https://github.com/<them>/homework-saathi.git
git push -u origin main
```

Before pushing, confirm nothing forbidden is tracked:

```bash
git ls-files | grep -Ei '\.(pdf|png|jpg)$|\.env$'   # must print NOTHING
```

---

## Step 5 — Create the Render service

**You cannot do this step** — it needs their login. Give them these instructions
verbatim and wait.

> 1. <https://render.com> → **New → Web Service**
> 2. Connect the `homework-saathi` repo
> 3. Runtime **Docker**, plan **Free**
> 4. **Environment → Add Environment Variable**, four of them:
>    - `GROQ_API_KEY` — required, nothing generates without it
>    - `CF_ACCOUNT_ID` — required
>    - `CF_API_TOKEN` — required, nothing retrieves without it
>    - `SAATHI_EMBED` = `cloudflare`
> 5. Create. The build is ~2 minutes (no model to download).

Tell them plainly: **free instances spin down after 15 minutes idle and take about
a minute to wake.** The first click on a cold link waits. If they are demoing to
someone live, open the link a minute beforehand.

Also tell them: create a **fresh** Cloudflare token directly in Render's UI and
delete the one currently in `.env`. That token is in a chat transcript.

---

## Step 6 — Verify the live box, do not trust it

```bash
cd "/Users/pradyumnawasthi/homework saarthi"
./.venv/bin/python scripts/preflight.py --url https://<service>.onrender.com
```

This checks health, that the models finished loading, that the backend is not the
stub, and that a real textbook page comes back inside the metered-data budget.

Then open the URL yourself and drive it once. It must:
- answer `तुल्य भिन्न क्या होती है?` with **four** parts and a chapter/page citation
- **refuse** `द्विघात समीकरण का सूत्र क्या है?` (Class 10 algebra) with a Hindi
  reason and no guess
- show the real page when *पेज देखिए* is tapped — on a deployed box this is
  fetched live from ncert.nic.in, ~2-4 s the first time per chapter

If the page image 404s, that is `pagesource`, not the app: the container fetches
chapter PDFs at request time. **ncert.nic.in has transient outages** — it was
fully unreachable (SSL connect failure) for several minutes on 19 Sep 2026 and
came back on its own. Retry before concluding anything is broken.

The fetch is bounded at 20s (`SAATHI_FETCH_TIMEOUT`) precisely because it runs
inside a web request and holds a lock; the page `<img>` has an `onerror` that
swaps in a Hindi line, so the parent sees a sentence rather than a broken image.

Do not "fix" an outage by shipping page images. That would breach the NCERT
licence, which is the whole reason this route exists.

---

## Step 7 — Put the link in the showcase

The showcase is a published artifact. **Republish the same file path** so the URL
stays the same — publishing a new one breaks every link already shared.

- File: `writeup/showcase.html`
- URL: <https://claude.ai/code/artifact/f228f2e5-aef2-425b-8699-3dbdefc6681f>

Add a prominent demo link near the masthead, and — because it matters for whoever
clicks it — the honest note that a cold free instance takes about a minute.

Also update:
- `HANDOFF.md` §8 artifact list, and its state percentage
- `README.md`
- `eval/DECISIONS.md` — close D19 with the measured cosine agreement from Step 2

Then commit in the project root with the repo's message style: a lowercase
`area: summary` subject, then what changed and *why*, and what remains unverified.

---

## Stop and ask the human when

- The Step 2 gate fails. It changes the project's headline numbers; the call is theirs.
- Any credential is missing or rejected.
- A step needs an account login (GitHub, Render, Cloudflare dashboard).
- The Docker build fails in a way that needs a dependency change — report the log,
  propose a fix, do not silently re-pin versions.
- You are about to do anything outward-facing that was not asked for.

---

## Traps, learned the hard way

Each of these cost real time. `eval/DECISIONS.md` has the full accounts.

- **The DOM is not the picture.** A body state class named `about` also matched the
  drawer's own `.about` rule, so `<body>` became `position: fixed` and slid
  off-screen. Every DOM query reported the content present, visible and correctly
  coloured. Only a screenshot showed a blank page. If something looks wrong,
  screenshot it and measure `getBoundingClientRect()`.
- **Don't test what you assume; test what deploys.** `vision.py` read page PNGs
  directly, so figure reading could never have worked in a container — while the
  docs promised it would. Found only by reading the code of a feature being
  switched off.
- **A check can be wrong about a folder that is fine.** `preflight` looked in
  `.dockerignore` to answer "is the book excluded?" and reported four failures
  against `deploy/`, where those directories are simply *absent* — the stronger
  guarantee.
- **`zsh` does not word-split unquoted variables.** `set -- $pair` inside a loop
  silently produces one argument, not two. Use Python for anything fiddly.
- **Groq's free tier is ~90 answers/day, per organisation.** Rotating the key does
  not reset it. `eval_generation.py` costs a third of a day per 30-question run.
  The retrieval, gate and audit evals need **no** LLM budget — prefer them.
- **Screenshots render `position: sticky` at the scroll offset**, inventing
  overlaps that are not there and hiding ones that are.
- **Playwright's `wait_for_selector` defaults to visible.** The inspector panel is
  hidden by design; use `state="attached"`.
