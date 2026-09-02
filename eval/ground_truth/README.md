# Text-layer ground truth

Hand-transcribed from 300dpi renders of the official NCERT PDFs, by reading the
rendered page. Four pages, chosen for maths density and layout variety:

| page | chapter | why this page |
|---|---|---|
| 18 | 2 — भिन्न | stacked fractions (¹⁄₂ as vertical layout), the safety-critical content |
| 78 | 6 — दुग्धशाला | digit-dense: two parallel worked solutions + a multiplication grid |
| 94 | 7 — आकार और प्रतिरूप | flowing geometry prose, low digit count |
| 183 | 15 — चित्रों के माध्यम से आँकड़े | prose + a bar chart holding data found nowhere in the text |

## What is scored, and what is not

`prose` holds the flowing sentences in reading order — the text that actually
gets embedded and handed to the model as grounding. It is scored for character
error rate and for numeric fidelity.

Deliberately **not** scored as prose:

- **2D arithmetic layouts** (vertical multiplication, boxed parallel solutions).
  Linearising these into a sentence is meaningless, so reading-order scrambling
  would swamp a naive full-page CER and tell us nothing about extraction quality.
  Whether they survive is recorded per method as a separate qualitative finding.
- **Figure-only data** (e.g. the p183 bar heights). Recorded in `figure_only` to
  size the refusal class described below.

## Transcription conventions

- A stacked fraction is written inline as `1/2`. The page shows numerator above
  denominator with a rule between; there is no `/` glyph on the page.
- `___` marks a printed fill-in-the-blank rule of any length.
- Text is transcribed as printed, including the book's own inconsistencies
  (p78 prints `1200` in a grid where sibling cells read `2,400` and `1,000`).
- `—` is the printed em-dash; `:` in `अत:` is the printed visarga-style colon.
