# Decision log

Decisions that were made by measurement rather than intuition, with the evidence
that settled them. PRD decisions are D1–D4; this file starts at D0 because the
first one had to be made before any of them could be implemented.

---

## D0 — Extraction path for the NCERT Hindi textbook

**Decision: hybrid.** Devanagari prose from Tesseract `hin` OCR over 300dpi
renders; every numeral taken from the PDF text layer; stacked fractions rebuilt
geometrically. Nothing is chunked from the raw text layer.

### Why this decision exists at all

The PDFs ship an embedded text layer, so ingest looks like a solved problem. It
is not. The books embed Kokila font subsets (25 in one chapter) with broken
ToUnicode maps, so extraction silently substitutes characters:

| page says | naive extraction |
|---|---|
| हम किसी **स्थान** पर | हम वकसी **्लथान** पर |
| पंच**भुज** (5 **भुजाओं वाली आकृति**) | पंच**िुज** (5 **िुजाओं िाली आकृ वत**) |
| **क्या** सम **त्रिभुज** | **्‍तया** सम **वरिुज** |

Ground truth came from rendering the page and reading it (`eval/ground_truth/`).

This matters beyond tidiness. Corrupt chunk text degrades BGE-M3 retrieval *and*
is pasted into the generation prompt as grounding — so the model would be
faithfully grounded on garbage while the §7.3 ungrounded-claim guardrail sees
nothing wrong, because the citation is genuine. It is §11.4's "silent, systemic
correctness failure that no amount of retrieval tuning would catch", one layer
earlier than the PRD anticipated.

### Measured bake-off

Four pages, hand-transcribed, chosen for maths density and layout variety.
Prose CER uses best-window alignment so reading order is not penalised.

| method | prose CER | numeric recall | fractions | invented numbers (p78) | s/page |
|---|---|---|---|---|---|
| **hybrid (chosen, final)** | **7.9%** | **91.1%** | **100%** | **0** | 0.3 |
| hybrid (first version) | 8.6% | 77.8% | 100% | 8 | 2.3 |
| ocr_tesseract_hin | 7.4% | 20.0% | 0% | 30 (**wrong values**) | 1.9 |
| ocr_tesseract_hin_eng | 9.0% | 24.4% | 0% | 28 (**wrong values**) | 2.4 |
| textlayer_pdftotext | 15.1% | 42.2% | 0% | 10 | 0.6 |
| textlayer_pymupdf | 15.6% | 82.2% | 0% | 2 | 0.0 |
| textlayer_pypdf | 27.7% | 82.2% | 0% | 23 | 0.1 |
| vlm_qwen2.5vl:7b | not viable — see below | | | | 980 |

### The finding that drove the decision

**Prose CER is the wrong headline metric for a maths product, and the method
that wins on it is the most dangerous one available.**

Tesseract `hin` has the best prose CER of any single method (7.4%, and 0.9% on
clean prose pages). It also destroys digits: its Devanagari model reads "1" as
danda (।/॥) and deletes it.

```
page:  150 (100 + 50) × 24 (20 + 4)   →   1,000    →   1 किलोग्राम ... ₹24
ocr:    50 ( 00 + 50) x 24 (20 + 4)   →     ,000   →   । किलोग्राम ... र 24
```

`150 → 50`. `100 → 00`. Adding `eng` recovers the `×` glyph but not the digits.
On p78 it lost 36 of 97 number tokens and emitted 30 numbers that are not on the
page — not blanks, but *plausible wrong numbers*, wrapped in prose that reads
perfectly. A parent teaching "the family saves ₹50 per kg" from a page that says
₹150 has no way to detect it. That is precisely the §8.1 asymmetry, and it means
a fluency metric would have selected the least safe pipeline.

The text layer is the mirror image: mangled Devanagari, but its digits are
Latin-encoded so the ToUnicode map works — 97/97 number tokens exact on p78,
zero missing. Hence the split: prose from OCR, numerals from the text layer,
merged positionally (PDF points ↔ render pixels at 300dpi). The hybrid loses
**zero** true numbers on p78.

### Stacked fractions: solved geometrically

No extractor recovered a single fraction, because `1/2` on the page is a
typographic layout — numerator, drawn rule, denominator — not a character. There
is no `/` glyph anywhere.

But the rule is a drawn path and the digits carry coordinates, so fractions can
be rebuilt: find short thin drawn paths, then require x-overlapping digits
centred above and below. On p18 this recovers **20/20** ground-truth fractions
with **no false positives** — the 3 extras it found are the pie-chart labels
(1/2, 1/4, 1/4), real fractions that had been classified as figure-only.

Deterministic, free, and with no model in the loop it has no hallucination
surface.

### Rejected, with reasons

- **Repairing the font CMap** — the principled fix, abandoned on evidence.
  Aligning text-layer output against OCR to learn the substitution yields 197
  distinct character pairs whose top 20 cover only 31%. The corruption is
  conjunct-level (`स्थ→्लथ`, `क्या→्‍तया`), not a 1:1 character permutation, so a
  learned character map cannot express it. A glyph-level repair per font subset
  would work but is brittle across chapters and editions.
- **Local VLM transcription (qwen2.5vl:7b via Ollama)** — not viable on this
  hardware. A 6.0 GB model on an 8 GB M1 swap-thrashes: 980 s/page, and it
  returned empty output within a 900 s request timeout. 190 pages would be ~52
  hours. Untested for quality, so it is recorded as an operational rejection,
  not a quality one. Consequence for the PRD stack table: the "Ollama fallback"
  line can only ever be a ~2B model on this machine.

### Known limits of the chosen path

- Prose CER is strongly page-dependent: 0.9% on clean prose (p94), 17.8% on
  figure-dense pages with callouts and rotated labels (p183). The gate should
  therefore be **per page**, not per book — pages that extract poorly become
  known content gaps that refuse, rather than pages that answer badly. This
  connects directly to §8.1: a content gap is a product input.
- The 8 residual invented numbers on p78 all come from 2D arithmetic blocks
  where OCR merges digits across a layout (`10 ( 2 × 5 )` → `225`). Those
  regions should take numerals verbatim from the text layer or be excluded from
  chunk text and served as the page image instead.
- Four ground-truth pages is a small sample, stated as a limitation. It is
  decision-adequate because the gaps between methods are large (2–4×), not
  marginal.

---

## D0.1 — A refusal class the PRD does not yet name: figure-only answers

Some questions in this textbook are answerable **only** from a figure. On p183,
question 3 ("शीला ने रमन की अपेक्षा अध्ययन पर कितने घंटे अधिक समय व्यतीत किया?")
depends on bar heights (शयन 8, विद्यालय 7, अध्ययन 4, भोजन और खेलना 3, अन्य 2)
that appear nowhere in any text on the page.

No text-based RAG pipeline can answer these, at any retrieval quality. They are
not a tuning problem, they are a category. They need to be labelled at ingest so
the confidence gate refuses them by construction — and the §8.1 refusal package
already does exactly the right thing for this case, since it sends the parent the
page image.

---

## D0.2 — Page mapping is asserted, not assumed

FR-10 quotes a page number the parent must find in a physical book, so the
mapping from PDF page to printed page is load-bearing. `scripts/fetch_ncert.py`
asserts every chapter's PDF length against the gaps between chapter start pages
transcribed from the printed विषय-सूची. All 15 chapters agree.

The check paid for itself twice on first run:

- **ch6** arrived as a truncated 192 KB fragment that still had a `%PDF` header
  and opened with 0 pages. Header-only validation passed it; the page-count
  assertion caught it. Downloads are now validated for a `%%EOF` trailer.
- **ch15**'s PDF carries 22 pages where the contents implies 12: it bundles the
  end matter (अधिगम सामग्री पत्रक, pp 191–200 — fraction-kit strips and cut-out
  polygons). Those are manipulatives with no explanatory prose, so they are
  recorded as a separate section and excluded from the retrieval corpus rather
  than silently chunked as if they were teaching text.

---

## D0.3 — Concept-unit segmentation follows the book's typography

**Decision: segment on the book's own coloured header pills, not on a character
count.** 190 pages became **222 chunks**, median 584 chars, across all 15 chapters.

PRD §11.3 says the retrieval unit is a concept unit — a worked example plus the
explanation that introduces it. To cut there you must find the boundaries.

**What does not work: font size.** Body text is 17.0pt and section headings are
17–19pt. A size threshold finds nothing.

**What works: filled boxes.** Measured across all 190 pages:

| fill | n | median height | what it is |
|---|---|---|---|
| (0.00,0.00,0.00) | 190 | 470 (exact, every page) | the "© NCERT / not to be republished" watermark |
| (1.00,0.87,0.58) | 100 | 29 | amber section-header pill (~7 per chapter) |
| (0.86,0.77,0.87) | 59 | 34 | lavender: header pills *and* शिक्षण संकेत boxes |
| others | — | — | figure fills |

Boxes are classified on shape and content rather than colour, so a restyled
edition still segments. 352 markers were found: 149 plain section headers, 62
activity headers, 23 teaching hints, 2 discussion prompts.

### The source inverts per region

Headers must come from the **text layer**, the opposite of body prose. Tesseract
is defeated by the decorative heading font on coloured fill — it read the p94
pill "टाइल्स लगाना व उन्हें प्रतिरूप में व्यवस्थित करना" as
`([ उकल्सलगना बड़नेंअतिलयमे सलस्थितकला )/` at **confidence 0–11**. The text layer
renders the same pill as `टाइल््स लगाना व उन्हें प्रतिरूप मेें व््यवस््थथित करना`,
which `scripts/textnorm.py` repairs deterministically by collapsing duplicated
combining marks. So: headers from the text layer, body prose from OCR, numerals
from the text layer, fractions from geometry. Four sources, each chosen where it
measurably wins.

### शिक्षण संकेत is the most valuable content in the book

The teaching-hint boxes are written **for teachers** and are the closest thing in
the textbook to "how to explain this to someone who doesn't get it" — which is
precisely answer-contract parts 2 and 4. 23 were extracted cleanly. They were
initially all dropped by a 16-word cap meant for header pills; asides now allow
paragraphs.

### Two safety rules that came out of reading the output

1. **Never emit a digit the text layer does not back.** Digits OCR produced with
   no matching text-layer number are dropped rather than kept. Every real number
   still arrives via the geometric re-insertion pass, so this only removes
   noise — and it moved numeric recall from 77.8% to **91.1%** while dropping
   invented numbers on p78 from 8 to **0**. Missing is recoverable; wrong is not.
2. **A gate must measure output risk, not input difficulty.** The first gate
   flagged pages where many numbers had to be re-inserted geometrically — and
   flagged **55% of the book**. That was measuring the mechanism working, since
   OCR deletes digits by construction. Re-pointed at `numbers_unverified`
   (digits with no text-layer backing), it flags **40/190 pages (21%)** — which
   lands just inside the §7.3 refusal-rate guardrail of 25%, a useful coincidence
   worth watching once retrieval is live.

### Chunk flags that feed the confidence gate

| flag | chunks | meaning |
|---|---|---|
| `needs_review` | 57 | page extraction below the quality gate |
| `figure_dependent` | 57 | content carried by a figure; unanswerable from text at any retrieval quality (D0.1) |
| `unverified_numbers > 0` | 190 | residual unbacked digits, ~2.6/page, mostly OCR debris |

### Known limitations, measured not hidden

- 7.9% prose CER is real and visible in the text: `पूर्ण` → `पर्ण`,
  `क्या` → `कया`, `टुकड़ों` → `ट्कड़ों`. Chunks are good enough for retrieval and
  keyword matching; they are **not** clean enough to quote verbatim to a parent.
  Generation must paraphrase from them, never echo them.
- Some fractions are consumed by substitution and drop out of the linear text
  even though the page-level reconstruction is complete (`1/4 के दो भाग 1/2 के…`
  lost both). Fraction *recall per page* is 100%; fraction *placement into prose*
  is not.
- Page-number stripping removes standalone matches of the chunk's own page
  number, so a genuine content "18" on page 18 would be lost. Rare, and
  preferred over folio numbers polluting every chunk.
- 17 of 222 chunks fall back to a chapter-level tag because neither header nor
  body hit the keyword vocabulary.

---

## D0 REVISED — the text layer wins after all, and OCR leaves the pipeline

**Decision: prose comes from the normalised text layer, not OCR.** Measured
**0.7% prose CER, 100% numeric recall, 100% fraction recall** — eleven times
better than the OCR hybrid's 7.9%, with no OCR in the pipeline at all.

### The mistake, stated plainly

D0 chose OCR for prose after comparing extractors **raw**. Two errors:

1. **I attributed pypdf's corruption to "the text layer" in general.** The
   destructive substitutions in D0's table — `स्थान→्लथान`, `भुजाओं→िुजाओं`,
   `क्या→्‍तया` — are **pypdf's**. PyMuPDF's corruption is a different and much
   milder class: duplicated combining marks (`हैैं`, `मेें`, `वर््ष`).
2. **I never applied the normaliser to body prose.** `scripts/textnorm.py` was
   written later, for section headers (D0.3), by which point OCR had already been
   chosen for prose. Duplicated marks carry no information, so collapsing them is
   lossless — and it fixes almost everything.

Applied to PyMuPDF's text layer, the normaliser takes prose CER from 15.6% to
1.13% immediately.

### Final comparison

| method | prose CER | numeric | fractions | s/page |
|---|---|---|---|---|
| **textlayer + normaliser + fractions (chosen)** | **0.7%** | **100%** | **100%** | 0.5 |
| hybrid OCR + text layer (previous choice) | 7.9% | 91.1% | 100% | 0.3 |
| ocr_tesseract_hin | 7.4% | 20.0% | 0% | 1.9 |
| textlayer_pymupdf raw | 15.6% | 82.2% | 0% | 0.0 |
| textlayer_pypdf raw | 27.7% | 82.2% | 0% | 0.1 |

Per page: p94 **0.00%**, p18 0.47%, p183 1.2%, p78 1.4%.

### What got it from 1.13% to 0.7%

Four fixes, each found by reading output rather than watching a metric:

1. **Never sort lines by y.** Keeping the PDF's own block order is worth 2.9 CER
   points (1.13% → 4.05% when y-sorted), because a global y-sort interleaves
   side-by-side layout blocks. This is the third time this exact mistake appeared
   in this pipeline; it cost 27 points in the OCR merge.
2. **Suppress only the fraction's own digit boxes**, not every digit in a span a
   fraction rect happens to touch. The broad version dropped real numbers from
   `35 ( 30 + 5 )` and took numeric recall to 68.9%.
3. **Splice fractions on x as well as y.** Matching on y alone dumped p18's
   pie-chart labels into body prose (`1/2 और 1/4 1/4 1/4`). Fractions that match
   no line horizontally are figure labels, kept in `figure_label_fractions`
   rather than injected into prose (D0.1).
4. **Collapse word-final doubled consonants** (`समान→समानन`,
   `विद्यालय→विद्यालयय`). Hindi writes real geminates with an intervening halant,
   so a bare doubled consonant with nothing after it is always an artifact.

### The one unrecoverable class, closed by hand

A conjunct whose second consonant was never mapped is genuinely absent
(`ग्राम→ग्ाम`) and cannot be repaired by rule. Across all 190 pages that is 657
occurrences but only **73 distinct tokens** — so it is closed by a hand-verified
map in `data/conjunct_repairs.json`, every entry read in context. Mostly a
missing rakar `र`; the rest missing `ष`, `ञ`, `व`, `य`, `ण`.

Scanning short frequent tokens then exposed a **second, separate class the halant
signature cannot see: a dropped *leading* letter** (`कक्षा→क्षा` ×27,
`कक्ष→क्ष` ×12). Two more entries; 75 in total.

Result: **0 unrepaired conjuncts across 30,478 Devanagari tokens.** The map
covers the entire book, and any future gap is detected automatically and becomes
the content-ops backlog rather than a silent error.

### Consequences

- **Tesseract, the Hindi traineddata, poppler and the 300dpi OCR pass all leave
  the ingest path.** Page renders are still produced, but only for the refusal
  package and FR-10. Ingest is now pure PyMuPDF plus two small deterministic
  passes — which strengthens the low-resource claim rather than weakening it.
- **The quality gate was re-pointed a third time.** v1 measured geometric
  re-insertion (flagged 55% of the book — the mechanism working, not failing);
  v2 measured OCR confidence (meaningless once OCR left); v3 measures
  `unrepaired_conjuncts`, which is exact rather than probabilistic. Flagged pages
  fell from 40/190 to **3/190**, and all three are genuinely figure-only
  (pp 188–190, the end-of-chapter figure spreads).
- **Chunks are now clean enough to quote.** The D0.3 warning that generation must
  paraphrase and never echo a chunk no longer applies at 0.7% CER. Verbatim
  quotation of a retrieved sentence is defensible.
- 190 pages → **248 concept-unit chunks**, median 652 chars, 0 unrepaired
  conjuncts, 1 needing review, 43 figure-dependent, 50 carrying inline fractions.

### The lesson worth keeping

Both times this pipeline went wrong, the cause was the same: **a metric computed
over the wrong unit looked authoritative.** A regex artifact-detector said pypdf
beat pymupdf 4×; raw-extractor comparison said OCR beat the text layer 2×. Both
were reversed once measured against hand-transcribed ground truth and once the
output was actually read. The ground-truth set has paid for itself three times
over, on four pages.

---

## D1-PRELIM — similarity alone cannot gate refusals, and §8.1's signal 3 is vacuous as written

Not the calibration experiment (that is week 3, and needs the full 150-question
labelled set). This is a cheap early probe of whether the refusal gate's core
assumption holds at all: **does retrieval similarity separate a Class 5 question
from a Class 9 one?**

`scripts/refusal_signal_probe.py`, 30 in-syllabus vs 8 out-of-syllabus/off-topic:

| set | min | mean | max |
|---|---|---|---|
| in-syllabus (30) | **0.496** | 0.632 | 0.750 |
| out-of-syllabus / off-topic (8) | 0.301 | 0.465 | **0.569** |

**The ranges overlap.** The lowest-scoring *legitimate* question — "टैनग्राम क्या
होता है?" at 0.496 — scores below Class 9 algebra at 0.569.

So a single similarity threshold is forced to choose between two failures:

- threshold at 0.55 → refuses real questions about tangrams (0.496), right
  angles (0.559) and quilt patterns (0.581)
- threshold at 0.49 → answers "x² + 5x + 6 का गुणनखंड कैसे निकालें?"

The second is the exact failure the PRD names in §2.2: a general assistant "will
answer a Class 9 question when asked a Class 5 one". Confirming it happens to our
own retriever, with numbers, is worth more than asserting it will not.

**Why algebra scores so high:** it asked about गुणनखंड (factorisation), which is
genuinely a Class 5 concept word in this book (`arithmetic.factors_multiples`).
The question is lexically near-in-syllabus while being pedagogically far outside
it. Embedding similarity cannot see that difference, and no amount of threshold
tuning will teach it to.

### The gap this exposes in the PRD

§8.1 lists four signals; signal 3 is "whether retrieved chunk metadata matches
Class 5 Maths". **That check can never fire.** Every chunk in the index *is*
Class 5 Maths, so the answer is always yes. As written it contributes nothing.

Two ways to make it real, to be decided at week 3:

1. **Query-side syllabus classification** — judge the question, not the chunk.
2. **Decoy indexing (preferred, and cheap)** — deliberately ingest a small set of
   Class 8–9 maths content tagged `class: 8/9`, purely so an algebra question
   retrieves a chunk whose metadata says "not Class 5" and can be refused on
   metadata rather than on score. It converts an unanswerable question about
   thresholds into a lookup, needs no model, and costs one extra ingest run.

Option 2 also gives the calibration set something to measure: the 30
out-of-syllabus questions in §8.1's labelled set currently have no chunk that
could correctly claim them.

### Margin looks more promising than raw score

Out-of-syllabus margins (top-1 minus top-5) cluster tight and low —
0.014–0.051, mean 0.028 — because a foreign question is roughly equidistant from
everything. In-syllabus margins spread wider, up to 0.174. Margin is not clean
enough alone either, but it carries information that raw score does not, and it
should be a first-class axis in the week 3 sweep rather than a secondary check.

---

## D1 — Refusal gate: calibrated, and the threshold turns out to be almost irrelevant

**Operating point: coverage 71% of answerable questions at a 1.4% wrong-answer
rate**, threshold 0.415, measured on the 150-question labelled set
(100 in-syllabus, 30 out-of-syllabus, 20 adversarial). Inside §7.3's
non-negotiable 2% budget.

This calibrates the **gate**, not the generator. §8.1's full curve needs
generation; the question here is narrower and comes first: should we answer at all?

### The curve, and the thing it reveals

| threshold | coverage | wrong-answer rate |
|---|---|---|
| 0.300 | 71.0% | 2.7% |
| 0.400 | 71.0% | 2.7% |
| **0.425** | **71.0%** | **1.4%** ✓ |
| 0.475 | 71.0% | 1.4% ✓ |
| 0.550 | 66.0% | 1.5% ✓ |
| 0.600 | 48.0% | 2.0% |
| 0.650 | 21.0% | 0.0% |
| 0.700 | 1.0% | 0.0% |

**The curve is flat from 0.30 to 0.475.** The similarity threshold barely
matters — it only needs to be a low floor around 0.42, just high enough to
exclude "भारत के प्रधानमंत्री कौन हैं?" at 0.414.

That is worth saying plainly, because §8.1 frames this decision as *sweep one
threshold and pick a point*. In practice the gate's behaviour is set almost
entirely by **categorical layers**, not by the continuous score. Tuning the
score alone got 26% coverage; getting the layers right got 71% with a *lower*
threshold. The trade-off named in the PRD is real, but it lives somewhere else
than expected.

### How it got from 26% to 71%

| gate design | coverage @ ≤2% wrong |
|---|---|
| similarity only | 1% |
| similarity + Class 5 metadata | 26% |
| + margin ≥ 0.03 | 25% |
| + chunk flags (`figure_dependent`, `needs_review`) | 22% |
| + query-side pre-checks (**layered**) | 26% |
| + numeric-answerability and chart-axis detection | **71%** |

Similarity alone reaches 1% coverage, which is the same as not shipping.

### Each refusal class needs its own mechanism

The single threshold was being forced up to 0.645 to catch leaks it could never
see. Broken down by class, the decoy/metadata signal is **perfect on what it was
built for** — 23/23 genuinely out-of-syllabus maths questions caught by metadata
alone: algebra 0/7, higher-number 0/8, geometry 0/2, statistics 0/2,
trigonometry 0/1, off-topic 0/3 wrongly matched Class 5. Everything else needed a
different tool:

| refusal class | mechanism that actually catches it |
|---|---|
| out-of-syllabus maths | decoy corpus + `class` metadata |
| other subject, general knowledge, off-topic | query-side topic vocabulary, plus a low similarity floor |
| missing context ("इसका जवाब क्या है?") | query-side: no maths topic word and no numbers |
| two questions at once | query-side: two interrogatives spanning two topic groups |
| answer-copying requests | query-side intent match — a §4 positioning decision, not a confidence one |
| ASR garble | query-side: any Latin letter beside Devanagari, then FR-2's confirmation turn |
| figure-value lookup | query-side: figure reference **and** value-seeking **and** not method-seeking |
| bar-chart data | chunk-side: chart-axis digit runs (see below) |

Query-side pre-checks catch **36 of 50** refusables before retrieval runs at all,
with **zero** false positives on the 100 answerable questions — deterministic
string work, no model, no latency, no cost.

### Two mistakes in my own signals, both caught by reading the failures

**1. Decoys by class label were conceptually wrong.** "Out-of-syllabus" is a
property of the **topic**, not of the book's class. Class 6-8 maths *revisits*
fractions, angles, large numbers and area, so a Class 7 fractions chapter is not
out-of-syllabus for a fractions question — it is the same concept taught later.
Indexing whole higher-class books captured **41 of 100 legitimate parent
questions** as top-1 and forced the gate to refuse 76% of real questions.

Restricting decoys to topics **verified absent** from the Class 5 corpus (19
markers, each checked for zero occurrences) cut 1,275 decoy chunks to 516 and
took legitimate-question capture from 41% to 21%. The dropped candidates make the
point: `गुणनखंड` (31 hits in Class 5), `क्षेत्रफल` (53), `चर` (60) are all Class 5
vocabulary — and `गुणनखंड` is exactly why a Class 9 algebra question scored 0.569
against the Class 5 corpus in the D1-PRELIM probe.

**2. `figure_dependent` was mis-calibrated in both directions.** As a gate signal
it discarded **12 of 100** answerable questions while still missing **3 of 4**
figure-only ones. The signal was in the wrong place: a figure-only question is
identified by the **question** pointing at a figure, not by the chunk containing
one. It remains as chunk metadata but no longer gates.

Refining that further mattered too: refusing on any figure reference broke
"नक्शे में दिशाएँ कैसे देखते हैं?" (a method question, answerable from text). A
figure reference now refuses only alongside a value-seeking interrogative and
never alongside कैसे / मतलब / क्यों.

### The finding that unlocked 71%: chart axis labels

One question defeated every signal — p183's
"शीला ने रमन की अपेक्षा अध्ययन पर कितने घंटे अधिक समय व्यतीत किया?" It names no
figure, and it retrieves exactly the right chunk. But its answer is in the bar
heights, which no text states.

A numeric-answerability check ("value-seeking question, chunk states no numbers")
should have caught it and did not — because **the chunk is full of numbers:
`8 7 6 5 4 1 0 … 10 9`, the chart's y-axis tick labels**, pulled in by the text
layer. The chunk looks numeric while containing no data.

So axis runs are now detected explicitly: four or more bare numbers with no words
between them is a chart axis or a table header row, not prose. **61 of 248 chunks
carry them.** They were noise in the chunk text *and* they were defeating the
answerability check. With them identified, a value-seeking question whose best
chunk is chart-axis-bearing is refused — and coverage went 26% → **71%**.

### Honest limits

- **Refusal on legitimate traffic is ~29%**, just outside §7.3's 25% guardrail.
  The binding constraint is no longer the threshold: **21 of 100 legitimate
  questions still retrieve a decoy chunk as top-1**. Narrowing the decoy corpus
  further, or requiring a decoy to beat the best Class 5 chunk by a margin rather
  than merely rank first, is the next lever.
- **One residual leak** at the operating point: "तीन भिन्नों को जोड़ना है जिनके हर
  अलग हैं" (adding unlike denominators, 0.639). Genuinely beyond Class 5, but its
  nearest chunk is the Class 5 section on *same* denominators, and no decoy covers
  it because Class 6-7 fraction chapters were filtered out as Class 5 topics.
- **The 100 in-syllabus questions are authored, not observed.** They use parent
  vocabulary deliberately ("एक जैसी भिन्न" rather than "तुल्य भिन्न") to avoid the
  seed set's flattery, but they are still my guesses about how parents speak. The
  discovery interviews are what make this set real.
- **This is the gate's curve, not §8.1's full curve.** Answer accuracy needs
  generation. A wrong-answer rate of 1.4% here means "1.4% of answered questions
  should have been refused" — not "1.4% of answers were factually wrong".

---

## D1-REVISED — the decoy corpus has a hard ceiling at 73% coverage

**Decision: keep the decoy corpus at "any beyond-Class-5 marker" (516 chunks) and
require a decoy to lead by 0.01 to disqualify a question.** Coverage 71% → **73%**
at the same 1.4% wrong-answer rate. Also added a query-side vocabulary check,
which is free and catches 17 of 20 higher-class maths questions with **zero** false
positives on the 100 legitimate ones.

### What was tried, and what it cost

The binding constraint after D1 was that 21 of 100 legitimate questions had a
decoy as their nearest neighbour. Three levers, all measured:

| lever | legit → Class 5 | out-of-syllabus → Class 5 | coverage @≤2% |
|---|---|---|---|
| rank only (D1 baseline) | 79% | 28% | 71% |
| **decoy must lead by 0.01** | 79% | 28% | **73%** |
| decoy must lead by 0.02+ | — | — | 15% |
| decoys must be *predominantly* beyond Class 5 (75 chunks) | **98%** | 52% | 15% |
| query vocabulary only, no decoys | — | — | 15% |
| query vocabulary + decoy lead 0.01 | — | — | 73% |

Two of these look like wins in isolation and are net losses:

- **Thinning the decoy corpus** to chunks that are predominantly beyond Class 5
  fixed legitimate capture almost completely (79% → 98%) and **gutted the gate**:
  out-of-syllabus questions reaching a Class 5 chunk went 28% → 52%, so the
  threshold had to climb and coverage collapsed to 15%. Decoy corpus size trades
  one error against the other; the loose corpus sits closer to the right point.
- **Query vocabulary alone** is a beautiful signal — 17/20, no false positives —
  but the 3 it misses are enough on their own to force the threshold up. It adds
  nothing on top of a tight margin, because at a 0.01 lead the decoys already
  catch what the vocabulary would. It is kept in the gate anyway: it is free,
  categorical, and independent of corpus contents.

### The ceiling is structural, and worth naming

The decoy-lead distributions genuinely overlap. Legitimate questions lost to
decoys have leads of 0.006–0.071 (median 0.024); correctly-caught out-of-syllabus
questions have leads of 0.006–0.292 (median 0.060). At a 0.02 gap you recover 8
legitimate questions and let 6 out-of-syllabus ones through — a net loss against a
2% budget that permits at most one wrong answer.

The reason is not tuning. **Class 6–8 books teach Class 5 topics at greater
length**, so they win on similarity for questions that are squarely Class 5:
"सम और विषम संख्या में क्या फर्क है?" lost to a Class 7 chunk by 0.071.

So the remaining gap is not a gate problem. It is that Class 5 chunks score too
*low* (0.493–0.750), not that decoys score too high — which points at retrieval,
and is what D3 addresses.

---

## D3 — Hybrid retrieval: chapter-level accuracy was hiding a chunk-level miss

**Decision: score chunks with dense cosine plus an IDF term-overlap signal at
weight 0.35, and hand the generator the top 3 Class 5 chunks rather than 1.**
Chunk-level relevance **77% → 87%**.

### The metric that lied

Retrieval was reported at 90% chapter-hit @1 and 97% @5, and the Step 2 exit
criterion passed on that basis. It was too coarse. Downstream, on a 30-question
generation eval, **10 of 30 questions had the model correctly decline** because the
passage it received did not contain the answer — the right chapter, the wrong
chunk inside it.

`scripts/eval_retrieval.py` measures the thing that matters instead — does the
context contain the concept the parent asked about — and needs no LLM budget.

### Two fixes, in order of size

1. **Pass top-3 chunks, not top-1.** §11.1 says "retrieve top-k … generate
   against retrieved chunks only", plural; passing one chunk was a straight gap
   against the design. Declines 10 → 8, conformance 43% → 50%.
2. **Add a lexical signal.** Dense similarity over a whole passage dilutes a
   single decisive term, and a parent asking what a word *means* is exactly the
   case where the word is the strongest evidence. Measured, dense-only vs hybrid:

   | lexical weight | chunk-level relevance |
   |---|---|
   | 0.00 (dense only) | 77% |
   | 0.20 | 80% |
   | **0.35 (in use)** | **87%** |
   | 0.50 / 0.80 | 87% (plateau) |

   Concretely: "धारिता या क्षमता कैसे मापते हैं?" now retrieves ch8 p113
   "क्षमता या धारिता मापना" — precisely the right section. "चित्रालेख क्या होता है?"
   reaches the five chunks that contain the word, none of which dense retrieval
   surfaced.

### One decline was correct, and my label was wrong

"दर्पण जैसी आकृति कैसे बनाते हैं?" — **no chunk anywhere in the corpus contains
दर्पण or सममित.** Chapter 10 teaches symmetry entirely through figures. The
question is not answerable from text at any retrieval quality, the decline is
right, and it was **my "answerable" label that was wrong**. That is D0.1's
figure-only class showing up in the labelled set, and the set needs re-labelling
for chapters 10 and 11.

**Limitation of the new metric, stated plainly:** the "key term" is chosen as the
rarest non-stopword in the question, which sometimes picks conversational framing
("बच्चे को होमवर्क मिला है") instead of the topic. Three of the four remaining
misses are that artifact, not retrieval failures, so true relevance is somewhat
better than 87%. The *relative* comparison holds, because both settings are
scored with the same terms.

---

## D2-REVISED — the validator was over-firing, and the honest number moved twice

Contract failures on a 30-question eval fell from **8 to 1** after fixing five
defects in my own checks. Overall conformance figures across the session:

| run | conformance | contract failures | gate refusals |
|---|---|---|---|
| v1, 8 questions, structure only | 88% | — | — |
| v1 + groundedness | 50% | — | — |
| **v2, 30 questions (baseline)** | **53%** | 8 | 6 |
| v3, validator fixed | 43% | **1** | 16 |
| v4, top-3 context | 50% | 1 | 14 |
| v5, hybrid retrieval | *invalid — daily token budget spent mid-run* | | |

The v3 dip is real and instructive: fixing the validator moved failures from
"contract" to "gate", because the model began *correctly declining* on questions
whose passages did not contain the answer. The pipeline got more honest before it
got better.

### The five defects, all mine

1. **A model success scored as failure.** Asked for a right angle, the model
   replied "पाठ में समकोण की बात नहीं है।" — an honest decline, exactly as the
   prompt asked — and was recorded as four missing parts plus "not Hindi". It now
   emits an explicit marker, returns a clean refusal, and logs a content gap,
   which is §8.1's ingest backlog rather than a generation defect.
2. **My own prompt seeded a false positive.** The prompt called the retrieved text
   "अंश", which also means *numerator*; the model echoed the word and was failed
   for jargon. The prompt now says "पाठ", and `अंश`/`हर` moved to advisory —
   both collide with extremely common ordinary words (`हर` = "every"). This alone
   caused 6 false failures in 30.
3. **Jargon the parent used themselves.** "धारिता या क्षमता कैसे मापते हैं?" was
   failed for saying धारिता. The rule exists to stop *us* introducing unfamiliar
   words (§8.2); a word the parent typed is vocabulary they already have.
4. **Numeric demands on non-numeric concepts.** "दर्पण जैसी आकृति कैसे बनाते हैं?"
   has a perfectly good worked example with no numbers in it. Requiring numbers
   there caused 4 false failures and would push the model to invent figures —
   directly against the groundedness rule.
5. **An absolute Devanagari floor.** A short honest decline is 23 Devanagari
   characters and was failed as "not Hindi" for being brief. Now proportional.

### The different-numbers rule, rewritten

Strict zero overlap is **unsatisfiable** for a conceptual question: asked what
multiplying by 10 and 100 does, no honest example can avoid mentioning 10. Mere
novelty is too weak: "जैसे 1/3 को 2 से गुणा करें तो 2/6 मिलता है" introduces 2 and
still re-derives the exact question. The rule now fails an example that reproduces
every number the question gave while adding at most one new one — transcription —
and passes one that brings fresh operands even while reusing the operator. Four
shapes are pinned as permanent selftest cases.

---

## D5 — The real free-tier limit is 200,000 tokens per DAY

Read off live response headers and error bodies, not from documentation:

| limit | value |
|---|---|
| tokens per minute | 8,000 |
| **tokens per day** | **200,000** |
| requests per day | 1,000 |

At ~2,200 tokens per answer with three-chunk context, the daily cap is **about 90
answers per day, for the whole organisation**. That is the §13.3 constraint, and it
is not the one the PRD assumed ("LLM requests-per-day"): the binding resource is
tokens, and retrieved context dominates them.

This has three consequences worth carrying into the cost model:

- **Context size is the cost lever, exactly as §13.3 predicted.** Going from one
  chunk to three raised tokens per answer by roughly half and cut daily capacity
  proportionally. The 10-point retrieval gain in D3 was bought with ~50% more
  tokens per answer — a real trade, not a free win.
- **A 25-parent pilot fits, barely.** At the §7.2 target of 4 questions per active
  parent per week, 25 parents is ~100 questions/week ≈ 14/day against a ceiling of
  ~90. Evaluation runs compete with the pilot for the same budget.
- **An exhausted budget must not look like a quality result.** A run that hit the
  daily cap recorded 13 errors and reported "20% conformance", which is
  meaningless. The client now distinguishes per-minute from per-day limits, retries
  the former, and aborts the run on the latter saying so explicitly.

---

## D6 — The parent's word is not the book's word

**Decision: expand every query with the textbook's vocabulary for any colloquial
term the parent used** (`retrieval.PARENT_TO_BOOK`, 22 entries, each audited).

This began as a suspected content gap and turned out to be a vocabulary gap —
the opposite conclusion, and it saved three chapters from being mislabelled.

### How it was found

D3 left 8 questions where the generator correctly declined. Auditing them against
the corpus (`scripts/audit_answerability.py`) produced this:

| concept | the parent's word | occurrences in the book | the book's word |
|---|---|---|---|
| map | नक्शा / नक्शे | **0** | मानचित्र (25), दिशा (47), मार्ग (32) |
| symmetry | सममिति / दर्पण | **0** | अक्ष (24), मोड़ (37), अभिकल्पना (25) |
| design (ch11) | डिजाइन | **0 in ch11** | वर्ग (54), चौकोर (11), टुकड़ (11) |

Chapter 14 is titled मानचित्र और अवस्थितियाँ and never once says नक्शा. Chapter 10
teaches symmetry by folding paper about an axis and **never names it**. The
content is entirely present; the words are not.

Of 51 colloquial terms a parent might plausibly use, **24 appear nowhere in the
textbook** — including बटा (as in "एक बटा चार" for 1/4, which §8.3 uses as its
own worked example of an ASR risk), वजन, पैसा, बाकी, तिकोना, दुगना, पहाड़ा.

This is §3.1 as a measurable retrieval failure rather than a persona note: the
parent speaks fluent Hindi but did not finish the schooling the book is written
for, so the register genuinely differs. Every mapping target was checked to occur
in the corpus before being used.

### Why this mattered more than it looks

I was one step from re-labelling chapters 10, 11 and 14's questions as
`figure_only` — recording "the book doesn't cover this" for content the book
covers thoroughly. That would have permanently depressed the measured ceiling and
hidden a fixable problem behind a plausible-sounding limitation.

**Two automatic ways of picking the "important" word failed first**, in opposite
directions, and both are recorded in the audit script so they are not retried:
"any content word present" passes "दर्पण जैसी आकृति" on the generic word आकृति;
"rarest word by IDF" selects verb inflections (घटाना, मापते, बदलें) because the
textbook conjugates differently than a parent speaks, flagging 15 questions on
grammar alone. Curated vocabulary on both sides avoids inferring what matters.

### A metric that punished the fix

Chunk-level relevance *fell* from 87% to 83% when query expansion was switched on
— because the metric asked whether the retrieved passage contains **the
question's own words**, and the entire point of expansion is to reach the book's
words instead. Corrected to accept the substitution it asked for:

| retrieval | chunk-level relevance |
|---|---|
| dense only | 83% |
| + lexical signal | 87% |
| **+ lexical + query expansion** | **90%** |

The three remaining misses are all the metric's framing-word artifact
(होमवर्क, आसान भाषा), not retrieval failures.

---

## D1-FINAL — the 73% ceiling was a ceiling on the retriever, not the gate

**Operating point: 84% coverage at a 1.2% wrong-answer rate** — threshold 0.545,
decoy margin 0.07. Refusal on legitimate traffic **16%**, inside §7.3's 25%
guardrail for the first time.

D1-REVISED concluded that "the fix is better retrieval, not a better gate" and
then left the calibration running on dense-only scoring. Recalibrating the same
gate on the retriever that actually ships (D3 + D6) moved everything:

| | coverage | wrong | refusal on legit traffic |
|---|---|---|---|
| D1 (dense, rank-only decoys) | 71% | 1.4% | 29% |
| D1-REVISED (dense, margin 0.01) | 73% | 1.4% | 27% |
| **D1-FINAL (hybrid + expansion, margin 0.07)** | **84%** | **1.2%** | **16%** ✓ |

The wider margin only became safe once Class 5 chunks outscored the decoys. On
dense-only scoring, any margin above 0.01 collapsed coverage to 15%; on
production retrieval the usable range runs to 0.08.

### Choosing 0.07 over the peak at 0.08

The margin sweep, at each margin's best threshold:

| margin | coverage | wrong | threshold |
|---|---|---|---|
| 0.05 | 82% | 1.20% | 0.545 |
| 0.06 | 83% | 1.19% | 0.545 |
| **0.07** | **84%** | **1.18%** | **0.545** |
| 0.08 | 85% | 1.16% | 0.545 |
| 0.09 | 74% | 1.33% | 0.670 |
| 0.12 | — | cannot reach 2% at any threshold | |

§8.1 step 4 says take the highest coverage inside the budget, which is 0.08 at
85%. **0.07 is used instead.** The plateau from 0.05 to 0.08 carries identical
headroom (~0.8pp under budget), and the collapse at 0.09 is caused by one
question out of 150 — on a set that small a single item is worth ~1.2pp of
wrong-answer rate. One point of coverage buys a step back from a cliff whose
position is set by a single label. Revisit when the set is built from real parent
questions rather than my guesses at them.

---

## D5 ADDENDUM — the daily token budget is per organisation, not per key

Rotating the API key does **not** reset the 200,000-token daily allowance: the
new key reported the same organisation id and the same spent budget. Worth
knowing before assuming a rotation buys a fresh quota.

Practical consequence: **evaluation and the pilot draw on one shared daily
budget of ~90 answers.** A 30-question conformance run costs ~66k tokens, a third
of the day, so the eval cadence in §12.1 ("runs on every prompt, chunking or
threshold change") is not affordable on the free tier as written. Either the
regression set shrinks, or the gate and retrieval evals — which need **no** LLM at
all (`eval_retrieval.py`, `calibrate_refusal.py`, `audit_answerability.py`) —
carry the routine cadence, with generation measured less often. Building those to
run budget-free was accidental at first and is now deliberate.

Current partial figure, honestly labelled: **57% contract conformance on 23 of 30
questions** before the run aborted on the daily cap (1 contract failure, 9 gate
refusals). The full number needs the next reset.

---

## D7 — Cost model: ₹0.065 per conversation, and §13.3's binding order was wrong

**Cost per conversation: ₹0.065 on-demand, ₹0.016 with caching and batching.
₹1.11 per active parent per month. The 25-parent pilot runs free with 6× headroom.**

§13.2 deliberately left the rate column blank and made filling it from live
pricing a build task; §13.3 said the interesting output is the break-even map, not
a single number. Both are now done, with every volume measured from this system
and every rate dated and sourced (`scripts/cost_model.py`, priced 3 Sep 2026).

### Measured volumes, from the provider's own accounting

| | value | how |
|---|---|---|
| prompt tokens | 2,080 | Groq `usage` block, observed 2,065 / 2,094 |
| completion tokens | 133 | observed 117 / 149 |
| **total per answer** | **2,213** | → exactly **90 answers per 200,000-token day** |
| retrieved context | ~54% of prompt | the §13.3 prediction, confirmed |
| TTS characters | 348 | median of 67 real generated answers |
| ASR seconds | 20 | **assumption from §13.2, not measured** — no voice loop yet |

**A correction worth recording.** Token counts were first inferred three ways that
disagreed by 1.6×: Groq's rate-limiter messages (2,066–2,379), a local Qwen2.5
tokenizer over real retrievals (3,814), and implied consumption from exhausting
the daily budget (~2,900). The local tokenizer was wrong — it over-counted prompt
tokens by 1.8×, because qwen3.8-27b tokenises Devanagari considerably better than
Qwen2.5. The client now records the provider's `usage` block, which is free and
settles it exactly. **The Devanagari tax is real but smaller than first claimed:
1.75 chars/token against ~4 for English, so ~2.3× the tokens per character, not
3.7×.**

### The break-even map, and where §13.3 guessed wrong

Assuming §7.2's target of 4 questions per active parent per week:

| layer | binds at | headroom |
|---|---|---|
| **Groq tokens/day (200,000)** | **~158 active parents** | 90 answers/day |
| Groq requests/day (1,000) | ~1,750 parents | 1,000 answers/day |
| Supabase 500 MB database | ~2,025 parents | ~417k transcript rows |
| PostHog events/month (1M) | ~7,267 parents | ~8 events/question |
| Groq tokens/minute (8,000) | burst only | 3.6 concurrent answers/min |
| **WhatsApp** | **never binds** | see below |

§13.3 expected the order **LLM requests → WhatsApp fees → Supabase storage**.
Measured, it is **LLM tokens → Supabase → PostHog**, and *requests* never bind
before tokens do — the limit is the token budget, not the call count.

### WhatsApp costs nothing, and that is a design consequence

Service messages sent inside WhatsApp's 24-hour customer-service window are
**free**, and this product is purely responsive: the parent asks, we answer, and
even the §7.1 accept-prompt ("क्या आपने बच्चे को समझा दिया?") lands inside the
same window. So `msg_fee` in §13.2's formula is **zero by design, not by luck** —
the positioning in §4 (a coach who responds, not a service that broadcasts) is
also the thing that removes the per-message line from the cost model.

It only reappears if the product starts nudging: a re-engagement message is a
*utility* template at ₹0.115 + 18% GST = ₹0.136. For 25 pilot parents, one weekly
nudge each is ₹13.57/month. Marketing-category messages are ₹0.8631 — 7.5× utility
— and should never be used here.

### Scale, honestly (§6.1 goal 4)

| scale | on-demand / month | with caching + batch |
|---|---|---|
| 25-parent pilot | ₹28 (in practice ₹0 — inside the free tier) | ₹7 |
| one school, 300 children | ₹334 | ₹84 |
| one district, ~50,000 children | ₹55,681 | ₹13,920 |
| **10 million parents** | **₹1.11 crore** | **₹27.84 lakh** |

At every scale WhatsApp stays ₹0 and the LLM is essentially the entire bill. That
is why caching and context selection are **the strategy rather than an
optimisation**: they are a 4× difference at 10M, which is the difference between a
fundable per-user cost and an unfundable one.

### The context/quality trade, priced

| context | tokens/answer | free answers/day | paid ₹/answer |
|---|---|---|---|
| top-3 (in use) | 2,213 | 90 | ₹0.065 |
| top-2 | 1,839 | 109 | ₹0.054 |
| top-1 | 1,464 | 137 | ₹0.043 |

Dropping to a single chunk buys ~45% more daily capacity and costs 7 points of
chunk-level relevance (D3). The three-chunk choice is written down here as a cost,
not assumed as free.

### The honest gaps

1. **Bhashini commercial rates are not published.** Free for non-commercial use;
   the published individual plan is ₹250/month for 50,000 TTS characters/day
   (~143 answers/day at our 348 characters), and beyond that it is "contact
   Bhashini". This is the largest unknown in the model and the thing to resolve
   before any scale claim. Note the shape of the risk: at district scale the
   voice layer could plausibly exceed the LLM layer, and it is the one line that
   cannot be modelled from public information.
2. **ASR duration is assumed, not measured** — there is no voice loop yet.
3. **qwen3.8-27b's own price is unpublished**, so Qwen3-32B ($0.29/$0.59 per M)
   is used as the closest proxy. A smaller model is likely cheaper, so the model
   probably **overstates** cost.
4. §12.1's "eval runs on every change" cadence costs ~66k tokens per 30-question
   run — a third of the daily budget. The retrieval, gate, audit and cost evals
   need no LLM at all and carry that cadence instead.

---

## D8 — Web chat (FR-8), and the bugs that only appear when you use the thing

**Built the public web chat.** Python standard library only — no Flask, no npm —
because this is a prototype on free tiers and a dependency that must be installed
before the demo runs is a worse demo. `scripts/serve.py` + `web/index.html`.

FR-8 is P0 and marked portfolio-critical for a concrete reason: the WhatsApp Cloud
API test number can only message pre-approved recipients, so WhatsApp cannot be
shown to a stranger with a link. The web chat is the demo.

Implements the conversation contract that does not need Bhashini: **FR-2** the
interpretation read back with a confirmation before answering, **FR-3** chapter and
page cited on every answer, **FR-4** the §8.1 refusal package, **FR-5** the four
parts rendered as four labelled parts, **FR-7** the post-answer prompt feeding
WPEC, **FR-10** the actual textbook page image on request. Voice is built for and
labelled as pending credentials rather than faked. Measured 3.8s end to end.

### Two bugs that only surfaced by using it

**1. The refusal offered an irrelevant page.** Asked for the quadratic formula,
the product refused correctly and then offered **page 105 — kilograms and grams**.
§8.1 says a refusal ships "the textbook page image for the relevant chapter", and
for an out-of-syllabus question there is no relevant chapter. Promising a page and
then showing an unrelated one is worse than saying plainly that this is not in the
Class 5 book, so off-topic refusals now get their own spoken line and no page.

**2. `भारत` matched `भार`.** "भारत के प्रधानमंत्री कौन हैं?" (who is India's Prime
Minister) passed the query gate as a maths question, because the topic word
**भार** (weight) matches the opening syllables of **भारत** (India). It then got
refused later on a similarity score and offered a page — a bad outcome reached by
two wrong turns.

This is the third appearance of the same Devanagari collision class
(`विभिन्न`/`भिन्न`, `टैनग्राम`/`ग्राम`), now from the other direction: I had guarded
the prefix but deliberately allowed suffixes, because Hindi inflects
(`भिन्नों` must match `भिन्न`). A following **consonant** means a different word,
so it is now blocked — except where it opens a known verb inflection
(`जोड़` → `जोड़ने`, `माप` → `मापना`), which the first version of the fix broke.

### It made the gate better, and the threshold irrelevant

| | before | after |
|---|---|---|
| refusables caught pre-retrieval | 36/50 | **41/50** |
| false positives on 100 answerable | 0 | **0** |
| usable decoy-margin plateau | 0.05–0.08 | **0.05–0.10** |
| cliff | 0.09 | **0.12** |
| coverage at ≤2% wrong | 84% | **85%** at 1.2% |
| similarity floor selected by the sweep | 0.545 | **0.300 — inert** |

**The similarity floor is now doing nothing.** The sweep selected the bottom of
its own range, because the query pre-checks catch the low-scoring leaks
categorically. It is kept low as defensive depth, and the real backstop is the
generator declining when its passages do not contain the answer.

That is the third time strengthening a categorical layer has made the tuned score
matter less — D1 found similarity alone reaches 1% coverage, D1-FINAL found the
73% ceiling belonged to the retriever, and here the threshold stopped mattering at
all. For a gate whose spec is written as "sweep a threshold", the repeated finding
is that almost none of the decision lives in the threshold.

Operating point: **coverage 85%, wrong-answer rate 1.2%, refusal on legitimate
traffic 15%** — decoy margin 0.08, chosen three steps back from the cliff at 0.12
since 0.08 and 0.09 both reach 85%.

---

## D9 — Voice loop wired (FR-1, FR-6), pending credentials

**Bhashini ASR and TTS are implemented and wired end to end.** The only thing
missing is a free non-commercial registration: `BHASHINI_USER_ID` and
`BHASHINI_API_KEY` in `.env`. Until they arrive, `/api/status` reports voice as
unavailable, the microphone is disabled with an honest tooltip, and text works
unchanged — nothing is faked.

### Two implementation decisions worth recording

**1. The inference endpoint and key are read, not hardcoded.** ULCA is a two-step
flow: a config call to `meity-auth.ulcacontrib.org` returns the inference
endpoint, an inference key and a `serviceId` per task; the compute call then goes
to that endpoint. Every public example hardcodes the endpoint and the key. A
hardcoded third-party credential is someone else's secret and will rotate without
warning, so both are read from the config response — which is cached for 24 hours,
because paying a second round trip per question would eat the §7.3 budget of 20s
p95 for the whole voice round trip.

**2. WAV is encoded in the browser, so there is no ffmpeg dependency.**
`MediaRecorder` produces WebM/Opus; Bhashini's ASR wants WAV or FLAC. The options
were a server-side transcode (a new binary dependency, on a machine where the
demo has to just run) or capturing raw PCM through Web Audio and encoding
16-bit mono WAV in JavaScript. The second keeps the prototype dependency-free.

The encoder is the one piece that could be silently wrong — a bad header produces
a file that looks fine and transcribes to nothing — so the same logic was ported
to Python and parsed with the standard library's `wave` module: 1 channel,
2-byte samples, 16 kHz, exact frame count, exact file length. The downsampler
averages across each window rather than taking the nearest sample, which costs
nothing and avoids aliasing on the 48 kHz → 16 kHz path most browsers will take.

### Why the confirmation turn now earns its keep

FR-2 was already built for text, where nothing can be misheard, and it looked
like ceremony. With voice it becomes the load-bearing safety step §8.3 describes:
"एक बटा चार" (1/4) misheard as "एक बटा चालीस" (1/40) silently changes the
question, and the parent cannot detect the substitution because they asked in the
first place. The transcript therefore goes into the confirmation turn, never
straight to an answer — one visible turn converts a silent wrong answer into a
correctable one.

Open question 1 in the PRD asks whether parents tolerate that extra turn. It is
now a thing the pilot can actually measure rather than speculate about.

---

## D10 — Voice is provider-agnostic, and Gemini was rejected on §14 grounds

**Decision: browser speech as the prototype fallback, Bhashini as the production
path, Gemini rejected.** Voice works today with no account, and switches to
Bhashini automatically the moment credentials appear — `/api/status` reports
whether the server has them and the interface picks the provider.

### Why not Gemini's free tier, which would have worked

It genuinely would: Gemini handles audio input and has TTS models, on a free tier
with no card. Two reasons it is the wrong default here.

1. **§14 names this exact risk** — "Free-tier data governance — prompts used for
   provider model training" — with the mitigation "prefer Groq or local for
   anything containing user text". Google AI Studio's free tier trains on
   submitted data. The submitted data here is a child's homework and a parent's
   recorded voice.
2. **It deletes the §8.4 argument.** The PRD's case for Bhashini is that it is "a
   sovereignty and data-governance decision, not only a cost one" — that this is
   the stack a real Indian edtech would evaluate. Replacing government language
   infrastructure with a US frontier API because a portal was confusing removes
   one of the strongest defensible positions in the project, and replaces a
   reasoned choice with a convenience.

"Bhashini is the production path, browser speech is the prototype fallback" is a
defensible sentence. "We used Gemini because Bhashini's portal was confusing" is
not.

### The browser as a fallback, and its honest limits

`SpeechRecognition` with `lang="hi-IN"` and `speechSynthesis` with a Hindi voice
cost nothing, need no account, and work immediately. Limits, stated rather than
discovered later: recognition is Chromium-only, Chrome's implementation sends
audio to Google's servers (so it is a fallback, not a privacy improvement over
Gemini — the improvement is that **Bhashini** is the default once connected), and
`speechSynthesis` depends on a Hindi voice being installed locally.

Two details that would otherwise bite:

- **Voices load asynchronously.** `getVoices()` returns an empty list on the first
  call in Chrome, so without an `onvoiceschanged` handler the Hindi voice is
  missed and the answer is read aloud in an English accent — a subtle failure
  that looks like a quality problem rather than a bug.
- **The rate is set to 0.92.** This is an explanation the parent is meant to
  repeat to a child, not a notification; default speed is too fast to follow and
  copy.

### The Bhashini path is untouched

Nothing was removed. `scripts/bhashini.py` still holds the ULCA two-step flow, the
browser still encodes 16 kHz mono WAV for it, and the mode selector prefers it
whenever the server reports credentials. Getting the keys remains worth doing —
it is the difference between the prototype's story and the product's.

---

## D11 — Browser testing (Playwright), and three bugs it caught immediately

**Added `scripts/test_ui.py`: 23 assertions driven through a real Chromium at
phone width.** Everything before it was tested with `curl`, which exercises the
server and none of the interface — and the interface is where the product is.

It also meant seeing the thing for the first time. Screenshots land in `/tmp/ui/`.

### It caught three bugs, two of them in the tests themselves

**1. A test that passed for the wrong reason.** Readiness was detected by waiting
for the status text to contain "तैयार" (ready) — which also matches
**"तैयार हो रहा है…"** ("still getting ready"). The test charged ahead before the
models had loaded, got a 503, and recorded it as a refusal. It only ever passed
because the server happened to be warm from a previous run. Now it waits on the
status dot's class, and the fix was verified against a deliberately cold server.

**2. A flaky assertion that blamed the interface for a generation outcome.** An
answer and a refusal both render `.part`, so on runs where the contract validator
rejected the answer the test counted one "part" and then spent 30 seconds waiting
for labels a refusal never has. It now distinguishes the two outcomes and reports
which it got — which also surfaces a real property: the same question does not
always clear the contract, because generation runs at temperature 0.2.

**3. Two false failures from measuring too early.** `document.fonts.check()` at
`domcontentloaded` reported the Devanagari webfont missing while it was still in
flight, and `naturalWidth` was 0 because the image had not decoded. Both now wait
properly — and the font check additionally measures rendered text width against a
different family, because `fonts.check()` can be optimistic about a face that is
loaded but not actually applied.

### And one real product bug

**The page image was 652 KB.** §3.1 says this parent's "data is metered and
intermittent", and FR-10 sends a textbook page on request and with every refusal.
Serving the 300 dpi ingest render straight to a phone is a real cost to the user.
The web path now downscales to a 1000 px progressive JPEG: **652 KB → 90 KB, 86%
less data**, cached in memory, with the 300 dpi originals untouched on disk for
ingest. The test asserts the served weight stays under 150 KB so it cannot drift
back.

### Legibility, raised on the PRD's own reasoning

Seeing it rendered made an omission obvious. §3.1 calls reading Devanagari "slowly
and with effort" **the single most consequential user attribute**, and the answer
is text the parent reads *aloud to a child* — so it should be the most legible
thing on the page rather than chat-default sizing. Body 17→18 px, answer text
19 px, the say-this-to-your-child line 20 px, labels 11→12 px. The test now
asserts a floor, so it cannot silently regress.

### What the suite covers

Page load and title · Devanagari webfont loaded **and applied** · warm-up
readiness · voice provider selection · example prompts · FR-2 confirmation shown,
quoting the question, and **blocking the answer until confirmed** · FR-5 four
labelled parts with part 4 distinct · FR-3 citation · legibility floor · no
horizontal overflow at 430 px · FR-10 image renders and is data-cheap · FR-6
listen control · FR-7 prompt and recording · FR-4 out-of-syllabus refusal with no
irrelevant page · dark mode painting its own background · zero JavaScript errors.

---

## D12 — The interface was showing the parent my internal variable names

**Rewrote `web/index.html`, put Hindi on every error path, and grew the browser
suite 23 → 34 assertions.** The trigger was one line visible in a D11 screenshot:

```
कारण: beyond_class5_topic_in_question
```

A parent with limited literacy, reading Devanagari slowly (§3.1), was being shown
an English snake_case identifier from `query_gate.py`. Every refusal did this.
§8.1 says a refusal must never be a bare no — but a refusal in a language the user
cannot read is *worse* than a bare no, because it looks like a malfunction.

### The fix is a translation layer the parent's side owns

There are **14 refusal reason codes** across the gate layers. Each now has a Hindi
line written as *what to do next*, not as a diagnosis:

| code | what the parent now reads |
|---|---|
| `beyond_class5_topic_in_question` | यह सवाल कक्षा 5 की किताब से आगे का है — इस किताब में यह नहीं सिखाया गया। |
| `out_of_syllabus_by_margin` | यह बात कक्षा 5 की किताब में नहीं, बड़ी कक्षा की किताब में मिलती है। |
| `no_maths_topic` | यह गणित का सवाल नहीं लगा। किताब के किसी विषय के बारे में पूछिए। |

The reason code is still carried in the response — it is how the refusal curve is
measured — it just stopped being user-facing. **A new assertion sweeps all
rendered text for `[a-z]+_[a-z_]+` and fails if any internal code reaches the
parent**, so this class of leak cannot come back through some path I did not think
to check by hand.

Server errors had the same problem in a quieter form: rate limits, warm-up 503s
and transcription failures reached the browser as English exception text. Every
client-facing error in `scripts/serve.py` now carries an `error_hi`.

### The sticky header was covering the answer, and the screenshot hid it

I took a full-page screenshot to check the rewrite and the header appeared drawn
across the middle of the answer card. I nearly dismissed it: **Chromium renders
`position: sticky` at its current scroll offset in a full-page capture**, which
puts a sticky header mid-image as a matter of course. Measuring instead of looking
settled it:

```
at rest after auto-scroll: { headerBottom: 118, covered: 1, scrollY: 292 }
scrolled to top:           { covered: 0 }
```

**Real bug.** Auto-scroll jumped to the bottom of the page, and the 118 px opaque
header then sat on whatever landed at the top — part 1 of a long answer. Two
changes: cards carry `scroll-margin-top: 126px` and a new card is brought to its
**top** rather than the page bottom (an answer is read from part 1 down, so this
is the better behaviour regardless), and the header compacts once scrolled,
returning the tagline's space to the answer. The assertion is a rect
intersection between `header` and every `.part p`, not a screenshot.

*Recurring lesson, third form:* D0 was a metric blind to the corruption it was
measuring; D11 was a test that matched the string for "not ready yet"; this was a
capture mode that renders the defect away. **The instrument has its own failure
mode, and it is usually silent.**

### Rewritten rather than patched

`web/index.html` had grown by accretion across D8–D11. Rewriting it cost less than
threading another change through it. What is new beyond the above:

- **`HOW IT WORKS`** — a dev toggle rendering gate layer, retrieval score, chunk
  id, context chunks, contract stats, latency and voice provider. The system was
  previously only inspectable from a terminal, which made it impossible to show
  anyone. Asserted hidden by default (`state="attached"`, since it is *meant* to
  be invisible — the default "visible" wait made this test fail for being right).
- `setBusy()` locks the composer while an answer is in flight — double-send
  previously fired two Groq calls, which on an 8,000 TPM free tier is a
  self-inflicted rate limit.
- A refusal now offers "दूसरा सवाल पूछिए" instead of ending the conversation.
- Safe-area padding and `theme-color` for both schemes.

### New assertions (23 → 34)

FR-2 "no" restores the question and re-enables the composer · vague question gets
a Hindi clarification · **no internal reason code visible to the parent** ·
dev panel hidden by default and reveals internals when asked · over-long question
refused in Hindi · empty send creates no turn · composer disabled during and
re-enabled after an answer · **sticky header covers no answer text**.

**34 passed, 0 failed.**

### The refusal was diagnosing the wrong cause in six of seven codes

Fixing the reason codes made a worse bug visible. `query_pre_check` emits **seven**
codes, and the whole layer was being given one framing sentence:

> यह सवाल कक्षा 5 की गणित की किताब में नहीं है… *(this question is not in the
> Class 5 Maths book)*

That is true for exactly one of them. Four (`too_short`, `no_maths_topic`,
`two_questions`, `asr_suspect_code_mixed`) mean *I could not understand the
question*, and two (`answer_copying`, `figure_value_lookup`) mean *I understood it
and am declining on purpose*. Ask "इसका जवाब क्या है?" — a vague question about
something in the book — and the system blamed the syllabus. §8.1 requires a
refusal to be actionable, and a wrong diagnosis is not actionable; it sends the
parent to look for a problem that isn't there.

Each code now gets a line that names its actual cause. Two are worth quoting
because they are the product's positioning reaching the parent for the first time:

- `answer_copying` → **मैं तैयार जवाब लिखकर नहीं देता — उससे बच्चा सीखता नहीं। यह
  पूछिए कि बच्चे को यह कैसे समझाएँ।** §2 says this is a coach for parents, not an
  answer service for children. It was in the PRD and in the gate, but never said
  out loud to the person who needed to hear it.
- `figure_value_lookup` → **इसका जवाब किताब के चित्र में है, जो मैं पढ़ नहीं सकता.**
  An honest statement of a real limitation beats a false one about the syllabus.

**A clarify is not a refusal.** Four of these are pre_check's `clarify` outcome —
invitations to ask again. They were rendered in the warning palette, which
overstates what happened. They now get a `.card.asking` in neutral amber, and a
test asserts a clarify outcome produces no `.card.refusal`.

**And the explanation is given once.** The server now returns `cause_explained`,
and the interface suppresses its own `WHY` line for those codes rather than
restating the same thing underneath. Asserted.

### One bug found and deliberately not fixed

`x2+5x+6 का हल क्या है?`, **typed**, classifies as `asr_suspect_code_mixed` and was
told "एक बार फिर बोलिए" — say it again — when nothing was spoken. The copy is
fixed to work for either input mode. The **classification** is still wrong: the
trigger is code-mixing, which typed algebra has too, and this fires before the
beyond-Class-5 vocabulary layer that should have caught it.

I did not tighten the classifier. The refusal *outcome* is correct for that
question, so the parent-visible harm was only the wording; changing the gate means
re-measuring against `eval/refusal_set.py`, and the whole point of D3 is that gate
thresholds get measured rather than guessed. Recorded here so it is a known issue
rather than a surprise.

**Suite now 37 assertions, all passing.**
