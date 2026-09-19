# Hyperframes Composition Brief: होमवर्क साथी / Homework Saathi

## Objective
A short launch-style brag film for Homework Saathi, built on one inversion:
**every AI brags about answering; this one's achievement is refusing.**

## Output
- Composition directory: `brag-output/composition/`
- Rendered video: `brag-output/brag.mp4`
- Format: landscape — 1920x1080
- Duration: 21.2s

## Source Material
- Project root: `/Users/pradyumnawasthi/homework saarthi`
- Primary files read: `web/index.html` (the live app), `writeup/showcase.html`
  (the case study), `README.md`, `eval/DECISIONS.md`
- Product name: होमवर्क साथी / Homework Saathi
- Tagline / strongest claim: *हम बच्चों के ट्यूटर नहीं हैं — हम माता-पिता के कोच हैं।*
- Key UI to recreate: the app's **answer card** — specifically its amber-washed
  `बच्चे से यह कहिए` block with a 4px inset amber left rule — and its
  **refusal card** with the red outlined `जवाब नहीं दूँगा` badge.
- Copy that must appear verbatim:
  - `Every AI brags about answering.`
  - `This one's best feature is refusing.`
  - `होमवर्क साथी`
  - `For parents who left school before the class their child is in.`
  - `बच्चे से यह कहिए`
  - `"बेटा, एक किलो में हज़ार ग्राम होते हैं।"`
  - `किताब: अध्याय 8 — भार और धारिता, पेज 111`
  - `जवाब नहीं दूँगा`
  - `यह सवाल कक्षा 5 की किताब से आगे का है — बड़ी कक्षा में यह आएगा।`
  - `द्विघात समीकरण का सूत्र क्या है?`
  - `1.1%` / `wrong-answer rate`
  - `हम बच्चों के ट्यूटर नहीं हैं — हम माता-पिता के कोच हैं।`
  - `We are not a tutor for children. We are a coach for parents.`
  - `homework-saathi.onrender.com`

## Creative Direction
- Tone preset: **polished**
- Creative direction: *quiet product film for something with real stakes*
- Interpretation: restraint is the concept. Real parents in government schools;
  a loud edit would be off-brand and disrespectful. Few scenes, long holds, soft
  crossfades (0.6–0.8s), no flashing, no zooms. Confidence through stillness.
- Angle: the user is a parent who left school before the class their child is now
  in, so they cannot detect a wrong answer — which is exactly why they are
  asking. A confidently wrong maths answer taught to a child is worse than "I
  don't know", so the system is built backwards from refusal. The number that
  matters is not accuracy but a **1.1% wrong-answer rate**. And the output is not
  an answer at all: it is a sentence the parent says out loud to their child.
- Hook: `Every AI brags about answering.` → `This one's best feature is refusing.`
- Outro: the Hindi positioning line with its English gloss, then the live URL.
- Avoid:
  - Generic SaaS language
  - Abstract filler visuals
  - Unrelated visual redesign
  - Anything that makes the refusal read as an *error* rather than integrity

## Visual Identity
- Background: `#F4F5F7` (the app's own paper grey)
- Surface: `#FFFFFF`
- Accent: `#A96E0F` amber (Maths Mela's section-header pill fill); wash `#FCF7EE`
- Secondary: `#6E5C8A` lavender (the textbook's शिक्षण संकेत callout fill)
- Refusal: `#A63328`, soft `#F8E7E3`, line `#E4C0B8`
- Success outline: `#2C6A58`
- Text: `#14161A` ink, `#4B4A52` secondary, `#78767F` muted
- Display font: **IBM Plex Serif** 600 (English), **IBM Plex Sans Devanagari** 600 (Hindi)
- Body font: **IBM Plex Sans**; **IBM Plex Mono** for eyebrows, labels, URL
- Visual references: rounded 16px cards, 1px `#DDD9D1` borders, soft shadow
  `0 6px 22px rgba(23,23,26,.05)`, pill badges, the 4px inset amber rule

## Storyboard
Use `brag-output/brag-plan.md` as the creative contract.

1. **The inversion** — 4.2s — two serif lines in the same position, the second
   replacing the first.
2. **Who it is for** — 4.0s — title lockup + the one supporting line + `कक्षा 5 · गणित` pill.
3. **The product, payoff first** — 5.6s — the real answer card assembling:
   frame → say-line → citation strip. Caption alongside.
4. **It refuses** — 4.0s — parent bubble → refusal card with red badge → `1.1%`.
5. **The line it is built on** — 3.4s — Hindi tagline, gloss, URL.

## Audio
- Audio role: warm, restrained bed — present, never driving
- Audio arc: enters quietly under the hook → warms as the answer card assembles →
  ducks under the refusal so it lands in stillness → fades out under the closing line
- Music: `assets/music/happy-beats-business-moves-vol-10-by-ende-dot-app.mp3`
- Music treatment: start at 0, low gain throughout, gentle lift in scene 3, duck
  in scene 4, fade out across scene 5
- Music cue guidance: preset shipped alongside at
  `assets/music/happy-beats-business-moves-vol-10-by-ende-dot-app.music-cues.json`
  (109.96 BPM). **Strong cue at 20.19s — land the closing tagline there.** Beat
  grid ≈0.545s; for sequential *text* use every other beat (~1.09s). Useful
  beats: 9.29 / 10.38 / 11.47 / 12.56 (answer card), 15.28 / 16.38 (refusal).
- Audio-reactive treatment: **none** — deliberately. Restraint is the concept and
  reactive motion would undercut it. Documented here as a creative decision, not
  an extraction failure.
- Audio-coupled moments:
  - Scene 3 — the say-line settling — one soft, dry cue
  - Scene 3 — the citation strip arriving — one quieter cue
  - Scene 4 — the refusal badge appearing — one dry accent, **not** an impact
- SFX selection guidance: at most three cues in the whole film. Match motion, not
  emphasis. Nothing percussive on the refusal or on the numbers.
- SFX analysis guidance: `~/.claude/skills/brag/assets/sfx/sfx-analysis.md` —
  prefer low high-frequency-risk files; this edit is quiet and any hiss shows.
- Exact SFX choice: Hyperframes decides filenames, timestamps and volume after
  the animation exists.
- Audio files: music already copied to `brag-output/composition/assets/music/`.

## Hyperframes Instructions
Load `hyperframes-core`, `hyperframes-animation`, `hyperframes-creative`,
`hyperframes-keyframes`, `hyperframes-cli`. This is the `/brag` workflow — do not
enter the hyperframes entry-point intent interview.

Requirements:
- Recreate real UI from the project (the answer card and refusal card above).
- All text must be readable in the final render: short label ≥0.8s settled, a
  sentence ≥0.3s/word. Devanagari needs slightly more, not less.
- 15–25s total.
- Include the music layer; three sparse SFX maximum.
- Lock the closing tagline to the 20.19s strong cue (±0.15s), marked `// beat-locked`.
- Snap the scene-3 and scene-4 sequential reveals to every *other* beat (±0.10s),
  marked `// beat-grid`.
- Audio-reactive is intentionally skipped — see above.
- Run `hyperframes check` before render.
