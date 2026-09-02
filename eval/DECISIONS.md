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
