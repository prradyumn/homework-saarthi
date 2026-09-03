# HANDOFF — Homework Saathi

**Read this first.** It is the complete working context for continuing this
project in a fresh session. Written 3 Sep 2026, updated after the Opus 5 continuation session. Everything below was measured, not assumed,
unless marked otherwise.

---

## 0. The person, and how they want to work

**Pradyumn Awasthi** — MBA student, AI Design Associate at ConveGenius.ai (Indian
K-12 edtech at government-school scale). A product person who ships. Building this
as a **portfolio project to land Product Associate / APM roles** — so the write-up,
the decision log, the measured curves and a clickable demo matter as much as code.

How they like to work — follow these exactly:

- **Verify by running it, don't assert it.** Execute on real data, show actual output.
- **Tell them when they're wrong.** If a PRD decision fails on contact with data, say so with evidence.
- **Never fabricate a number.** "I haven't measured this" is a fine answer.
- **Flag scope creep** against the non-goals list.
- **Status updates in plain language** with a % complete — they asked for it "in a very
  normal manner so I can understand everything". They are smart; they want clarity,
  not jargon.
- They approve steps in batches ("do the next steps", "proceed"). They handle all
  account signups themselves and will do them when asked — just ask clearly.
- Explain engineering trade-offs in product terms.

## 1. The project in one paragraph

A Hindi, voice-first homework helpline on WhatsApp for **parents** of Class 5
government-school children — not for the children. Many of these parents left
school before the class their child is now in. The product is RAG over the child's
actual NCERT Class 5 Maths textbook, and its output is not an answer but **a
sentence the parent can say to their child**. Positioning line that settles every
feature argument: *we are not a tutor for children; we are a coach for parents.*

**The hard problem is not generation — it is knowing when to refuse.** A
confidently wrong maths answer that a parent teaches their child is far worse than
"I don't know", because the parent asked precisely because they cannot detect the
error. The refusal threshold is calibrated empirically (PRD §8.1).

Full spec: `PRD — Homework Saathi.pdf` in the project root (19 sections). The
Google Doc link in the original brief needs auth — use the PDF.

## 2. Hard constraints

- **Zero paid APIs.** No card, no paid tier, no expiring trial credits. This is a
  product asset (cost-per-conversation for users who cannot pay) and a sovereignty
  decision (Bhashini over a US frontier API). Never propose a paid path.
- **Hardware: 8 GB M1 MacBook Pro.** This killed the 7B local VLM (980 s/page,
  swap-thrash). Local models must be ~2B. BGE-M3 peaks at 3.3 GB while encoding.
- **Scope: Class 5 Maths, Hindi, one district.** Non-goals: other subjects,
  classes, languages; student-facing mode; native app; accounts/login; homework
  photo OCR. Don't widen scope to be helpful — narrow is the point.
- **Edition: NCERT गणित मेला (Maths Mela), NCF-2023, 15 chapters, 190 prose pages.**
  User confirmed: "ncert and make it newest version". Official codes at
  `ncert.nic.in/textbook/pdf/ehmm1{01..15}.pdf`.
- **Never redistribute the textbook** — pages carry "© NCERT / not to be
  republished". `ingest/raw`, `ingest/pages`, `ingest/extracted` are gitignored.

## 3. State: ~60% of the project

| Milestone (PRD §15) | Status |
|---|---|
| Wk 1 — parent interviews | **0%** — only the user can do these. The whole thesis is gated on Q11 (do parents accept "I don't know"?) and the Card A/B test (kill criterion: <5 of 8 pick Card B → re-scope to answer-verification). |
| Wk 1 — golden set 100 Q + 150 refusal set | 30-Q seed golden set done; **150-Q refusal set done** (`eval/refusal_set.py`). Full 100-Q Track A golden set with *verified answers* not done. |
| Wk 2 — ingest + retrieval | **DONE, exit criterion passed** (97% chapter hit @5 on seed). |
| Wk 3 — refusal calibration + curve | **DONE** — **84% coverage at 1.2% wrong**, refusal on real traffic 16% (inside the 25% guardrail). Curve published and updated. |
| Wk 4 — answer contract + Hindi generation + Track A | **Contract + validator + live Groq path done; validator debugged (contract failures 8→1 of 30).** Conformance 50%, gated by retrieval not generation. Track A accuracy (needs verified answers) not done. |
| Wk 5 — Bhashini voice + WhatsApp + web chat | **Web chat DONE** (`scripts/serve.py` + `web/index.html`, stdlib only, 3.8s end to end). **Bhashini wired and untested** — needs only `BHASHINI_USER_ID` / `BHASHINI_API_KEY` in `.env`. WhatsApp not started. |
| Wk 6 — pilot, Track B panel, write-up | Decision log and curve write-up exist; pilot not started. |

## 4. Accounts and secrets

| Service | Status |
|---|---|
| **Groq** | ✅ Key received from user, stored in `.env` (gitignored, chmod 600), loaded by `scripts/answer.py`. **The key was pasted into chat — remind the user to rotate it at console.groq.com/keys.** |
| Supabase | ❌ Not yet. User said "later we will also set a postgres and supabase". Retrieval runs on a local numpy index (`ingest/index_combined.npz`) which is fine for now. |
| Bhashini | ❌ **This is the only thing blocking voice.** Code is written and wired (`scripts/bhashini.py`); register free for non-commercial use at bhashini.gov.in (ULCA portal) and put `BHASHINI_USER_ID` + `BHASHINI_API_KEY` in `.env`. Everything degrades cleanly without them. |
| WhatsApp Cloud API | ❌ Not yet. Needed for Wk 5. |
| PostHog | ❌ Not yet. |

## 5. Repo map

```
scripts/
  fetch_ncert.py         fetch official PDFs; ASSERTS pdf page counts vs printed contents
  render_pages.py        300dpi PNGs of 190 pages (for refusal UX / FR-10 only now)
  extract_textlayer.py   THE extractor: PyMuPDF text layer + normaliser + fractions
  textnorm.py            deterministic Devanagari repair (+ 75-entry map)
  extract_hybrid.py      legacy OCR hybrid; still provides fraction_groups_detailed()
  structure.py           header pills / callout boxes / figure regions from filled boxes
  extract_corpus.py      190 pages -> ingest/corpus.json (with per-page quality gate)
  chunk.py               corpus -> 248 concept-unit chunks (ingest/chunks.json)
  concept_tags.py        Hindi keyword -> concept_tag vocabulary
  fetch_decoys.py        Class 6-9 out-of-syllabus decoy corpus (516 chunks)
  embed_and_eval.py      BGE-M3 embed + seed retrieval eval
  refusal_signal_probe.py  D1-PRELIM: similarity alone can't separate
  build_refusal_set.py   eval/refusal_set.py tuples -> refusal_set.json
  query_gate.py          query-side pre-checks (deterministic, pre-retrieval)
  calibrate_refusal.py   the sweep -> accuracy_vs_coverage.csv + report
  answer_contract.py     four-part contract validator (§10)
  answer.py              END-TO-END: gate -> retrieve -> generate -> validate -> answer/refuse
  eval_generation.py     contract-conformance eval per backend
  textlayer_bakeoff.py   the extraction comparison harness
  ocr_tuning.py          OCR config sweep (historical)
  serve.py               THE DEMO: stdlib web chat (FR-8). `python scripts/serve.py`
  bhashini.py            ASR + TTS via the ULCA two-step flow (FR-1, FR-6)
  cost_model.py          cost per conversation + the break-even map (D7)
  retrieval.py           lexical/IDF signal + parent->textbook vocabulary (D3, D6)
  eval_retrieval.py      chunk-level relevance, needs NO LLM budget
  audit_answerability.py are the labelled questions actually answerable?
web/index.html           the chat interface, Hindi-first, browser WAV recording
data/conjunct_repairs.json   75 hand-verified conjunct repairs (auditable content-ops)
eval/
  DECISIONS.md           EVERY decision with evidence and reversals — read it
  ground_truth/          4 hand-transcribed pages (the source of all CER numbers)
  golden_seed.json       30 seed questions
  refusal_set.py/.json   the 150-question labelled set
  accuracy_vs_coverage.csv, refusal_calibration.json
  generation_groq.json, generation_ollama.json
  textlayer_bakeoff.json, retrieval_report.json, ocr_tuning.json
writeup/refusal-curve.html   published artifact source
ingest/  manifest.json, corpus.json, chunks.json, decoy_chunks.json, index_*.npz,
         ocr_cache/ (gitignored raw/pages/extracted)
README.md, requirements.txt, .env (gitignored), .venv/
```

## 6. Decisions made, with headline numbers

Full evidence in `eval/DECISIONS.md`. Summary:

**D0 (REVISED) — Extraction.** Prose from PyMuPDF text layer + deterministic
normaliser + 75-entry conjunct map; fractions rebuilt from drawn-rule geometry.
**0.7% prose CER, 100% numeric recall, 100% fraction recall.** No OCR in the
pipeline. Original D0 wrongly chose OCR (7.9% CER) because (a) I attributed
pypdf's substitution corruption to "the text layer" generally — PyMuPDF's
corruption is milder duplicated-marks, and (b) I never applied the normaliser to
body prose. 0 unrepaired conjuncts across 30,478 tokens.

**D0.1 — Figure-only refusal class.** Some questions are answerable only from a
figure (bar heights). Not a tuning problem; a category. PRD doesn't name it.

**D0.2 — Page mapping asserted.** All 15 chapters' PDF lengths match printed
contents. Caught a truncated ch6 download and ch15's bundled end-matter (pp 191–200
cut-out sheets, excluded).

**D0.3 — Segmentation follows header pills, not font size.** Body 17pt, headings
17–19pt → size is no signal. Filled coloured boxes are. Watermark = black fill,
exactly 470pt tall, on every page. 248 chunks, 23 शिक्षण संकेत teaching hints.

**D1 — Refusal gate: 71% coverage at 1.4% wrong, threshold 0.415.** The curve is
FLAT from 0.30–0.475: the similarity threshold barely matters; categorical layers
do the work. Similarity alone → 1% coverage. Query pre-checks catch 36/50
refusables pre-retrieval, 0 false positives. Decoy metadata catches 23/23 genuine
out-of-syllabus maths.

**D2 — Answer contract.** Validator enforces §10 as code. Model chosen by
measurement: **`qwen/qwen3.8-27b` on Groq: 50% conformance (with groundedness),
median 1.4s, p95 15.3s.** `openai/gpt-oss-120b`: 0% (answered in English).
Local `qwen3:1.7b`: 25%, p95 44.8s — safe but nearly useless.

**D6 — Parent vocabulary ≠ book vocabulary.** Of 51 colloquial terms, **24 appear
nowhere in the textbook**. नक्शा: 0 occurrences, though ch14 is titled
मानचित्र और अवस्थितियाँ. सममिति: 0, though ch10 teaches symmetry by folding about an
अक्ष and never names it. Fixed with a 22-entry audited query expansion
(`retrieval.PARENT_TO_BOOK`), NOT by relabelling those chapters unanswerable —
which is what I was one step from doing.

**D1-FINAL — gate coverage 73% → 84% at 1.2% wrong**, floor 0.545, decoy margin
0.07. No gate logic changed: the 73% ceiling was a ceiling on the *retriever*.
Chose 0.07/84% over the spec-rule peak of 0.08/85% — same headroom, one step back
from a cliff set by a single label.

**D3 — Hybrid retrieval.** Dense cosine + IDF term overlap at weight 0.35, and
the generator gets the **top 3** Class 5 chunks (§11.1 says "chunks", plural — I
had passed one). Chunk-level relevance **77% → 87%**. Chapter-level accuracy (97%)
was hiding a chunk-level miss: 10 of 30 questions had the model correctly decline
because its passage lacked the answer. `scripts/eval_retrieval.py` measures this
with **no LLM budget**.

**D5 — Real Groq free-tier limits: 8,000 tokens/minute AND 200,000 tokens/DAY**
≈ **90 answers/day** at ~2,200 tokens each. Not requests/day as the PRD assumed.
Three-chunk context costs ~50% more tokens than one — the D3 gain was bought, not
free. A spent daily budget once produced a meaningless "20% conformance"; the
client now aborts on the daily cap.

**D4-PRELIM — Tiering.** Groq primary → local 2B for offline demo → refuse.
Contract validator makes a weak fallback *safe* (refuses rather than misleads).
Measured: the local tier refuses 75% of the time. Consider a second free API
rather than local for resilience — but vet data policy (§14).

## 7. Things a new session must NOT re-learn the hard way

1. **Never sort text lines by y-coordinate.** Keep PDF block order. This bug appeared
   THREE times and cost 27, 27 and 2.9 CER points.
2. **pypdf ≠ pymupdf.** The destructive substitutions (`स्थान→्लथान`) are pypdf's. Use PyMuPDF.
3. **Tesseract `hin` deletes every "1"** (reads it as danda): 150→50, 1000→000. Never
   trust OCR for digits. OCR is no longer in the pipeline at all.
4. **Fractions are geometry**: `1/2` is numerator + drawn rule + denominator, no `/`
   glyph. `fraction_groups_detailed()` rebuilds them. Filter only the exact digit
   boxes it names, not the enclosing rect.
5. **Decoys must be by TOPIC, not class label.** Class 6–8 revisit fractions/angles.
   Whole-book decoys stole 41/100 legit questions. 19 verified-absent markers in
   `fetch_decoys.py`; `गुणनखंड`, `क्षेत्रफल`, `चर` ARE Class 5 vocabulary.
6. **Figure-only detection belongs on the QUESTION, not the chunk.** Chunk-side
   `figure_dependent` discarded 12/100 answerable while missing 3/4 figure-only.
   Query-side: figure reference + value-seeking (कहाँ/कितने) + NOT method-seeking (कैसे/मतलब).
7. **Chart y-axis tick labels leak into chunk text** as digit runs (`8 7 6 5 4 1 0`).
   61/248 chunks. Detected by `AXIS_RUN` in chunk.py → `has_chart_axis`. This was
   the unlock from 26% → 71% coverage.
8. **Devanagari substring collisions**: `विभिन्न` contains `भिन्न`, `टैनग्राम` contains
   `ग्राम`. Every keyword regex uses prefix guard `(?<![ऀ-ॿ])`. Python `\b` is useless
   (`\w` includes matras).
9. **Danda `।` is punctuation, not a digit** — treating it as digit-ish rewrote every
   full stop into a nearby number.
10. **Groq model names move.** `llama-3.3-70b-versatile` is gone. Query `/v1/models`.
    Real free-tier limit off live headers: **8,000 tokens/minute** (not req/day). At
    ~1,500 tokens/call ≈ 5 calls/min. `eval_generation.py` paces at 13s.
11. **Contract conformance ≠ groundedness.** A perfect 4-part answer cited 360°/108°
    that the Class 5 page never states. Groundedness check exists on digit-form
    numbers in parts 1–2 only (word-form numbers like "एक शून्य" are descriptive prose).
12. **Different-numbers rule is strict zero-overlap** by design (§8.1 asymmetry). Known
    false-refusal on "multiply by 10 and 100" questions. Fix is prompt-side: keep
    operator, change operand. A relaxation was tried and reverted (let transcription through).
13. **Hindi word-numbers** (`एक तिहाई`=1/3) are detected and fractions reduced (2/6→1/3)
    for the different-numbers rule, but deliberately NOT for groundedness.
14. **The 8 GB M1** cannot run a 7B model. `qwen2.5vl:7b` is installed but useless here.
    `qwen3:1.7b` runs. BGE-M3: 1.84 GB load, 3.3 GB encode peak, 0.04s/query.
15. **All in-syllabus test questions are authored by me, not observed from parents.**
    Retrieval numbers are optimistic until the discovery interviews produce real questions.
16. **My own validator over-fires; check that before blaming the model.** Five separate
    defects found by reading output (D2-REVISED): an honest decline scored as 4 missing
    parts; my prompt's word for "passage" (अंश) also means "numerator" so the model was
    failed for echoing it; jargon the *parent* used flagged as ours; numeric demands on
    non-numeric concepts (symmetry); an absolute Devanagari floor failing short replies.
17. **Chapter-level retrieval accuracy is a misleading metric.** 97% @5 coexisted with a
    third of questions getting the wrong chunk. Use `scripts/eval_retrieval.py`.
18. **Never let a spent API budget be recorded as a quality score.** `RateLimitExhausted`
    aborts the run; `RateLimited` retries after a pause.
19. **Some questions are genuinely unanswerable and my labels were wrong.** No chunk in
    the corpus contains दर्पण or सममित — ch10 teaches symmetry purely through figures.
    Chapters 10 and 11 need re-labelling in the refusal set.
20. **The decoy ceiling is structural (73%).** Thinning decoys fixes legitimate capture
    (79%→98%) but guts out-of-syllabus catch (28%→52%) → coverage 15%. Don't retry this.

## 8. Environment

- `./.venv/bin/python` — Python 3.14, deps in `requirements.txt` (pymupdf, pypdf,
  pdfminer.six, rapidfuzz, pillow, numpy, torch, sentence-transformers).
- BGE-M3 cached in `~/.cache/huggingface/hub` (2.1 GB).
- Ollama installed (brew); models: `qwen3:1.7b` (1.4 GB), `qwen2.5vl:7b` (6 GB, unusable).
  Start with `ollama serve &` before `--backend ollama`.
- Tesseract + `hin`/`Devanagari` traineddata installed but NO LONGER USED by the pipeline.
- OCR cache in `ingest/ocr_cache/` (190 TSVs) — only needed by the legacy hybrid.
- Git: 10 commits on the default branch, author set via `-c user.email=pradyumn@convegenius.ai`.
- Published artifacts (source in `writeup/`, redeploy by republishing the same path):
  - refusal curve: https://claude.ai/code/artifact/892a72b8-4f10-42e4-bc33-8b0c834f8715
  - cost model:    https://claude.ai/code/artifact/7d339f21-4f6a-42a6-a83e-d07acdcbef74
- **Scope, as the user set it: a PROTOTYPE on free tiers, not a scalable product.**
  Do not add Supabase (the local numpy index is sufficient), do not do scale
  engineering. The cost model exists because the PRD asks for the 10M figure.

## 9. How to run everything

```bash
cd "/Users/pradyumnawasthi/homework saarthi"
./.venv/bin/python scripts/fetch_ncert.py          # PDFs + page-map assertions
./.venv/bin/python scripts/render_pages.py         # 300dpi pages
./.venv/bin/python scripts/extract_corpus.py       # -> corpus.json (~2 min)
./.venv/bin/python scripts/chunk.py                # -> chunks.json
./.venv/bin/python scripts/fetch_decoys.py         # -> decoy_chunks.json
./.venv/bin/python scripts/calibrate_refusal.py --embed   # -> curve (~5 min embed)
./.venv/bin/python scripts/answer.py --selftest    # contract validator
./.venv/bin/python scripts/answer.py --question "1 किलोग्राम में कितने ग्राम होते हैं?"
./.venv/bin/python scripts/eval_generation.py groq # conformance eval
./.venv/bin/python scripts/textlayer_bakeoff.py --no-vlm  # reproduce CER table
```

## 10. Next steps, in order

1. **Re-run `eval_generation.py groq --n 30 --tag v7` once the daily budget
   resets.** Two runs aborted on the 200k TPD cap. Last full figure: 50% (v4);
   last partial: **57% on 23 of 30** (v6, everything fixed) — partial, not a score.
   NOTE: the daily budget is **per organisation**, so rotating the key does not
   reset it. A 30-question run costs ~66k tokens, a third of the day.
2. **Do NOT relabel ch10/ch11 as figure-only** — that was investigated and was
   wrong (D6). The content is present under different words.
3. **Then chase conformance.** Remaining failure modes, in order of frequency:
   `passage_does_not_cover_question` (retrieval, ~6 of 23), `answer_is_in_a_chart`
   (correct refusals), `unglossed_jargon` (1). The `ungrounded_number` failures are
   gone. Lever for the rest: few-shot with the GOOD example from `answer.py`
   selftest, and continued retrieval work.
4. **Track A golden set with verified answers** (100 Q). Answers must be verified
   against the book — numeric exact-match where possible. Then measure answer
   *accuracy*, not just conformance. That gives §8.1's full curve.
5. **Supabase pgvector** when the user signs up — swap `index_combined.npz` for a
   table; no logic change.
6. **Week 5**: Bhashini ASR/TTS + FR-2 confirmation turn + WhatsApp Cloud API test
   number + public web chat (FR-8, portfolio-critical). Needs user signups.
7. **Voice**: the moment Bhashini credentials land, run `python scripts/bhashini.py`
   (prints availability) then `--speak` to write a test WAV. The browser records
   16 kHz mono WAV in JS — encoder maths verified against Python's `wave` module —
   so there is no ffmpeg dependency. Measure the §7.3 20s p95 round trip once live.
8. **Write-up**: DECISIONS.md is the raw material. The refusal curve artifact exists;
   D0's extraction bake-off and D2's model comparison deserve the same treatment.

## 11. PRD amendments the evidence supports

Tell the user these when relevant; several are already flagged to them:

- §11.2 "Ollama (Gemma or Qwen) local" → must say ~2B; 7B doesn't run on 8GB.
- §8.1 signal 3 "retrieved chunk metadata matches Class 5" is vacuous without decoys
  (every chunk IS Class 5). Decoy corpus makes it real.
- §8.1 framing "sweep one threshold" understates it: the gate is categorical layers;
  the threshold is nearly irrelevant.
- §13.3 "LLM requests-per-day" → the binding limits are **8,000 tokens/minute and
  200,000 tokens/day** (~90 answers/day). Context size is the cost lever, exactly as
  §13.3 predicted: three-chunk context costs ~50% more tokens than one.
- §11.1 "retrieve top-k → generate against retrieved chunks" — implement as plural;
  passing a single chunk caused a third of questions to be declined.
- Retrieval needs a lexical signal alongside dense embeddings (D3), which §11.1
  does not mention.
- Add a **figure-only** refusal class (D0.1).
- Add **query-side pre-checks** as a gate layer (36/50 refusals, pre-retrieval, free).
- §10 different-numbers rule needs operator/operand nuance for "multiply by 10" questions.
- Chunks are clean enough (0.7% CER) to quote verbatim — a constraint I earlier
  imposed and then retracted.

## 12. Honest open problems

- Refusal on legitimate traffic ≈ 29% (guardrail 25%).
- One residual gate leak: "तीन भिन्नों को जोड़ना है जिनके हर अलग हैं" (unlike denominators).
- Generation conformance 50% (v4 full) / 57% (v6 partial, 23 of 30). Gated by
  **retrieval**, not generation.
- Chunk-level retrieval relevance 90%; the 3 remaining misses are artifacts of the
  metric's key-term picker choosing conversational framing, not real misses.
- §12.1 says the eval runs on every prompt/chunking/threshold change. At ~66k
  tokens per 30-question run against a 200k/day org budget, that cadence is
  unaffordable. The retrieval, gate and audit evals need NO LLM and now carry it.
- The riskiest assumption (parents accept "I don't know") is entirely untested.
- All test questions are authored, not observed.
