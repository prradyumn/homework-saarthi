# Homework Saathi

A Hindi, voice-first homework helpline on WhatsApp for **parents** of Class 5
government-school children — not for the children.

Many of these parents left school before the class their child is now in, so they
cannot help with homework. This is retrieval-grounded (RAG) over the child's
actual NCERT Class 5 Maths textbook, and its output is not an answer but
**a sentence the parent can say to their child**.

> We are not a tutor for children; we are a coach for parents.
> "Show the child a video" fails that test. "Give the parent a sentence to say" passes.

Full product spec: `PRD — Homework Saathi.pdf`. Decisions made by measurement,
with the evidence that settled them: [`eval/DECISIONS.md`](eval/DECISIONS.md).

---

## The hard problem is not generation. It is knowing when to refuse.

A confidently wrong maths answer that a parent then teaches their child is far
worse than "I don't know" — because the parent asked *precisely because* they
cannot detect the error. So the refusal threshold is calibrated empirically
(PRD §8.1): a 150-question labelled set, sweep the threshold, plot accuracy
against coverage, take the highest coverage that keeps wrong answers ≤ 2%.

Everything in this repo is built to make that curve trustworthy. Which is why the
first real problem turned out to be one layer below retrieval.

---

## What is built so far

**Ingest (PRD milestone week 2) — done.** 190 textbook pages → 248
concept-unit chunks.

| measure | value |
|---|---|
| prose character error rate | **0.7%** |
| numeric recall | **100%** |
| stacked-fraction recall | **100%** |
| unrepaired corrupt tokens | **0** of 30,478 |
| pages needing review | 3 of 190 (all genuinely figure-only) |

**Retrieval (week 2 exit criterion) — in progress.** BGE-M3 embeddings running
locally, evaluated against a 30-question seed golden set covering all 15 chapters.

Not started: refusal calibration (week 3), the answer contract and Hindi
generation (week 4), the Bhashini voice loop and WhatsApp/web delivery (week 5),
the 25-parent pilot (week 6). And the eight to ten parent interviews, on which
the whole thesis is gated.

---

## The finding that shaped the ingest

The NCERT Hindi PDFs ship an embedded text layer, so ingest looks solved. It is
not: the embedded fonts have broken character maps, and extraction **silently
substitutes characters**.

| the page says | naive extraction |
|---|---|
| हम किसी **स्थान** पर | हम वकसी **्लथान** पर |
| पंच**भुज** (5 **भुजाओं वाली आकृति**) | पंच**िुज** (5 **िुजाओं िाली आकृ वत**) |

That matters far beyond tidiness. Corrupt chunk text degrades retrieval *and*
gets pasted into the generation prompt as grounding — so the model is faithfully
grounded on garbage while the ungrounded-claim guardrail sees nothing wrong,
because the citation is genuine. It is exactly the PRD's §11.4 risk: a silent,
systemic correctness failure that no amount of retrieval tuning would catch.

**So nothing is chunked until an extraction path clears a measured bar.** Ground
truth is four pages hand-transcribed by reading 300dpi renders
(`eval/ground_truth/`), chosen for maths density and layout variety.

### Prose fidelity is the wrong headline metric for a maths product

The single method with the best prose CER was the most dangerous one available.
Tesseract's Hindi model reads `1` as a danda and deletes it:

```
page:  लगभग 150 किलोग्राम … ₹24 × 150 … 10 और 100 से गुणा
ocr:   लगभग  50 किलोग्राम … र 24 »  50 … 10 और  10 से गुणा
```

`150 → 50`. On one page it lost 36 of 97 numbers and emitted 30 numbers that are
not on the page — not blanks, but *plausible wrong numbers*, wrapped in prose
that reads perfectly. A parent teaching "₹50 per kg" from a page that says ₹150
cannot detect it. A fluency-led metric would have shipped the least safe pipeline.

### Stacked fractions are geometry, not text

`1/2` on the page is a typographic layout — numerator, drawn rule, denominator.
There is no `/` glyph anywhere, and no extractor recovered a single fraction.
But the rule is a drawn path and the digits carry coordinates, so fractions are
rebuilt geometrically: find short thin drawn paths, require x-overlapping digits
centred above and below. 20/20 on the ground-truth page, no false positives, no
model in the loop and therefore no hallucination surface.

### Chunks follow the book's typography, not a character count

The retrieval unit is a concept unit — a worked example plus the explanation that
introduces it. Font size carries **no** signal (body 17pt, headings 17–19pt);
the headings sit inside coloured pills, so segmentation follows those. The
watermark is identifiable because it appears on all 190 pages at exactly 470pt.

This also surfaces the **शिक्षण संकेत** teaching-hint boxes — written *for
teachers*, and the closest thing in the book to "how to explain this to someone
who doesn't get it", i.e. precisely what the answer contract needs.

### A refusal class the PRD does not yet name

Some questions are answerable **only** from a figure. One page asks how many more
hours Sheela studied than Raman; the answer lives solely in bar heights that
appear in no text on the page. No text-based RAG can answer these at any
retrieval quality — it is a category, not a tuning problem. They are labelled at
ingest so the confidence gate refuses them by construction, and the refusal
package already does the right thing, since it sends the parent the page image.

---

## Running it

### The demo

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python scripts/serve.py              # then open http://localhost:8000
./.venv/bin/python scripts/serve.py --backend stub   # canned answers, spends no tokens

./.venv/bin/python scripts/preflight.py          # is this box able to serve? (17 checks)
./.venv/bin/python scripts/test_ui.py            # 51 browser assertions (needs --backend stub)
```

Deploying it to a link a stranger can open is **[DEPLOY.md](DEPLOY.md)**. The
short version: Hugging Face Spaces, because BGE-M3 peaks near 3.3 GB while
encoding and every other free tier caps out around 512 MB.

### Rebuilding the corpus from scratch

```bash
./.venv/bin/python scripts/fetch_ncert.py        # official PDFs + page-map assertions
./.venv/bin/python scripts/render_pages.py       # 300dpi renders (refusal UX, FR-10)
./.venv/bin/python scripts/extract_corpus.py     # 190 pages -> corpus.json
./.venv/bin/python scripts/chunk.py              # -> 248 concept-unit chunks
./.venv/bin/python scripts/embed_and_eval.py --embed   # BGE-M3 + retrieval eval

./.venv/bin/python scripts/textlayer_bakeoff.py --no-vlm   # reproduce the CER table
```

The textbook is **not** redistributed here — pages carry "© NCERT / not to be
republished". The repo ships the fetch script and derived artifacts, never the book.
The *deployed* app holds to the same line: when a parent asks to see a page,
`scripts/pagesource.py` fetches that chapter's PDF from ncert.nic.in at request
time and renders the single page asked for, so the bytes come from NCERT's own
server and this app redistributes nothing.

### Layout

| path | what |
|---|---|
| `scripts/fetch_ncert.py` | fetch, and assert PDF page counts against the printed contents |
| `scripts/extract_textlayer.py` | the chosen extractor: normalised text layer + fractions |
| `scripts/textnorm.py` | deterministic Devanagari repair |
| `data/conjunct_repairs.json` | 75-entry hand-verified map for the unrecoverable class |
| `scripts/structure.py` | header pills, callout boxes, figure regions |
| `scripts/chunk.py` | concept-unit segmentation + metadata |
| `scripts/concept_tags.py` | Hindi keyword → `concept_tag` vocabulary |
| `scripts/textlayer_bakeoff.py` | the measured extraction comparison |
| `eval/DECISIONS.md` | every decision, with the evidence and the reversals |

---

## Cost

Zero paid APIs, and not as a hobbyist constraint: for a product whose users
cannot pay, cost per conversation is a design constraint that decides whether the
thing can exist at all. Choosing Indian government language infrastructure
(Bhashini) and locally-run embeddings over a frontier US API is also a
sovereignty and data-governance decision, not only a cost one.

Ingest currently runs on a **₹15,000-class laptop** — an 8 GB M1 — in about two
minutes for the whole book, using no OCR and no network calls after fetch. That
is a stronger low-resource claim than the PRD originally made, and it is measured.

---

## Two things this repo is honest about

**The riskiest assumption is untested.** The entire safety design assumes parents
accept "I don't know" from a machine. Discovery question 11 tests it. If parents
reject it, the product needs a different shape — answer *verification* rather
than answer generation.

**Two metrics in here were wrong before they were right.** A regex
artifact-detector said pypdf beat pymupdf by 4×; a raw-extractor comparison said
OCR beat the text layer by 2×. Both reversed once measured against
hand-transcribed ground truth, and the second reversal removed OCR from the
pipeline entirely. Both reversals are written up in `eval/DECISIONS.md` rather
than quietly corrected, because the reasoning is the point.
