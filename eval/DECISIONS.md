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
| **hybrid (chosen)** | **8.6%** | **77.8%** | **100%** | **8** (page no., list markers) | 2.3 |
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
