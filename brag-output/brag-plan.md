# Brag Plan: होमवर्क साथी / Homework Saathi

## What is this app?
A Hindi homework helpline for the **parents** of Class 5 government-school
children in India — not the children — that answers only from the child's own
NCERT textbook, and refuses when it cannot.

## The angle
**Every AI product brags about answering. This one's achievement is refusing.**

That inversion is true, not a line. The user is a parent who left school before
the class their child is now in, so they *cannot detect a wrong answer* — which
is exactly why they're asking. A confidently wrong maths answer, taught by a
parent to a child, is worse than "I don't know". So the whole system is built
backwards from refusal, and the measured number that matters is not accuracy but
a **1.1% wrong-answer rate**.

Specific to this project and no other: the product's output is not an answer at
all. It is a sentence the parent says out loud to their child.

## Hook (first 2-3 seconds)
One line, dark on the textbook's own paper-grey:
**"Every AI brags about answering."**
Hold. Then it is replaced, same position, same weight:
**"This one's best feature is refusing."**

No motion tricks. The claim does the work.

## Key moments (the middle)
- **The real answer card**, payoff-first, exactly as the app renders it: the
  amber-washed *बच्चे से यह कहिए* block with its inset left rule, carrying
  "बेटा, एक किलो में हज़ार ग्राम होते हैं।" — then the citation
  *किताब: अध्याय 8 — भार और धारिता, पेज 111* arriving beneath it.
- **The refusal**, as the app actually renders it: the *जवाब नहीं दूँगा* badge in
  the refusal red, with the real Hindi sentence declining Class 10 algebra and
  pointing the parent somewhere useful.
- **1.1%** — the wrong-answer rate, set as a hero number against the refusal.

## Outro / punchline
The positioning line the whole product is built on, in Hindi with its gloss:

> हम बच्चों के ट्यूटर नहीं हैं — हम माता-पिता के कोच हैं।
> *We are not a tutor for children. We are a coach for parents.*

Then the live URL, small.

## User flow worth showing
Entry → key action → result, and it is the centrepiece:
1. A parent types/speaks a question in Hindi (the composer, mic as the primary control).
2. The answer card returns **payoff-first** — the sentence to say to the child.
3. The same parent asks something out of syllabus and is **refused**, with a reason.

Scenes 3 and 4 are the working app, not a landing page.

## Tone
- Preset: **polished**
- Creative direction: *quiet product film for something with real stakes*
- Interpretation: restraint throughout. These are real parents in government
  schools; a chaotic meme edit would be both off-brand and disrespectful. Few
  scenes, long holds, soft crossfades, no flashing. Confidence through stillness.

## Format: landscape — 1920x1080
## Duration: 21.2s

## Visual identity (from the project)
- Background: `#F4F5F7` (`--ground`, the app's own paper grey)
- Surface: `#FFFFFF`
- Accent: `#A96E0F` (amber — lifted from Maths Mela's section-header pill fill)
- Secondary: `#6E5C8A` (lavender — the textbook's शिक्षण संकेत callout fill)
- Refusal: `#A63328`
- Text: `#14161A` ink, `#4B4A52` secondary
- Display font: **IBM Plex Serif** (600) for English; **IBM Plex Sans Devanagari** (600) for Hindi
- Body font: **IBM Plex Sans**; **IBM Plex Mono** for labels and the URL
- Strongest visual element: the answer card's amber `बच्चे से यह कहिए` block with
  its 4px inset left rule, and the refusal card's outlined red badge

## Share copy (draft)
Most AI demos brag about answering. I built one whose best feature is saying
"I don't know" — because its user is a parent who can't tell when it's wrong.

## Audio direction
- Role: warm, restrained bed — present but never driving
- Music: `happy-beats-business-moves-vol-10-by-ende-dot-app.mp3` (109.96 BPM, the
  slowest bundled track, which suits the tone)
- Music treatment: start at 0, enter under the hook at low gain, hold steady,
  gentle lift into the answer card, duck slightly under the refusal so the red
  badge lands in relative quiet, fade out over the final tagline
- Music cue guidance: preset read from `cues/…vol-10….music-cues.md`. Strong cue
  at **20.19s** — land the closing tagline there. Beat grid is ~0.545s; for any
  sequential *text* use every **other** beat (~1.09s) so lines stay readable.
  Useful beats near the reveals: 9.29, 10.38, 11.47, 12.56 (answer card) and
  15.28, 16.38 (refusal).
- Audio-reactive treatment: **none**. Restraint is the concept; waveform
  reactivity would undercut it.
- SFX posture: sparse. At most three cues in the whole film.
- Audio-coupled moments: the say-line settling into place; the citation arriving
  beneath it; the refusal badge appearing (one dry, quiet accent — not an impact).
- Restraint rule: no whooshes, no risers, no stings on the numbers. Nothing may
  make the refusal feel like a *failure* — it is the product working.

## Storyboard

### Scene 1 — The inversion — 4.2s
Paper-grey field. Centred serif line: **"Every AI brags about answering."**
Settles fast (0.4s), holds ~1.5s. Soft crossfade in place to:
**"This one's best feature is refusing."** Holds ~1.7s.
Sequential/interaction: none — two sequential lines in the same position.
Audio intent: music enters quietly under the first line; no accent.
Audio-coupled idea: none.
Music: low, warm, unobtrusive.
Transition mood: soft crossfade → Scene 2

### Scene 2 — Who it is for — 4.0s
Title lockup: **होमवर्क साथी** (Devanagari, large) with **HOMEWORK SAATHI** as a
small mono eyebrow beneath. One supporting line in serif:
**"For parents who left school before the class their child is in."**
Small amber pill, mono: `कक्षा 5 · गणित`.
Sequential/interaction: title settles, then the supporting line, ~1.09s apart.
Audio intent: bed continues; a single soft lift as the title lands.
Audio-coupled idea: none.
Transition mood: soft crossfade → Scene 3

### Scene 3 — The product, payoff first — 5.6s
Recreate the real answer card on white, rounded 16px, soft shadow. Top row: mono
`साथी` with a green outlined `किताब से` badge.
Then the hero block — amber wash `#FCF7EE`, 4px inset amber left rule:
label `बच्चे से यह कहिए`, and the sentence
**"बेटा, एक किलो में हज़ार ग्राम होते हैं।"**
Beneath, the citation strip: `किताब: अध्याय 8 — भार और धारिता, पेज 111`.
A small serif caption sits beside the card: *"The output isn't an answer. It's a
sentence you say to your child."*
Sequential/interaction: **yes** — card frame, then the say-line settling in, then
the citation strip. Space reveals ~1.09s apart (every other beat) and hold the
completed card ≥1.5s.
Audio intent: warmest point of the film; the payoff should feel arrived-at.
Audio-coupled idea: one soft, dry cue as the say-line settles; one quieter cue as
the citation arrives.
Transition mood: soft crossfade → Scene 4

### Scene 4 — It refuses — 4.0s
Same card geometry, refusal state. Red-outlined badge `जवाब नहीं दूँगा`.
Body, the app's real Hindi:
**"यह सवाल कक्षा 5 की किताब से आगे का है — बड़ी कक्षा में यह आएगा।"**
The question that triggered it shown small above in the lavender parent bubble:
`द्विघात समीकरण का सूत्र क्या है?`
To the side, a hero number: **1.1%** with a mono caption `wrong-answer rate`.
Sequential/interaction: **yes** — parent bubble, then the refusal card, then the
number. Beats ~15.28 and ~16.38; hold the full frame ≥1.4s.
Audio intent: music ducks slightly; the refusal lands in relative quiet. It must
read as *integrity*, never as an error.
Audio-coupled idea: one dry, quiet accent on the badge. No impact sound.
Transition mood: soft crossfade → Scene 5

### Scene 5 — The line it is built on — 3.4s
Paper-grey again. Devanagari, serif-weight:
**हम बच्चों के ट्यूटर नहीं हैं — हम माता-पिता के कोच हैं।**
Gloss beneath in smaller sans: *We are not a tutor for children. We are a coach
for parents.* Land this on the **20.19s** strong cue.
Then, small and mono, low: `homework-saathi.onrender.com`
Sequential/interaction: Hindi line, then gloss, then URL.
Audio intent: music fades out under the gloss; end close to silence.
Audio-coupled idea: none. Let it end quiet.
Transition mood: fade to end.

**Music mood for this video:** warm, restrained, unhurried — never triumphant.
**Audio summary:** A quiet bed enters under the hook, warms as the answer card
assembles, ducks so the refusal lands in stillness, and fades out under the
closing line — three sparse cues in total and nothing that turns a refusal into
a failure sound.
