# Bhashini API access — use case description

*Paste the body below into the ULCA application. Figures are measured, not
estimated; the sources are named so a reviewer can check them.*

---

## Homework Saathi — a Hindi voice helpline for parents of Class 5 government-school children

### 1. What the application does

Homework Saathi is a Hindi, voice-first homework helpline delivered over WhatsApp
and the web. Its user is not the student. It is built for the **parent** of a
Class 5 child studying in a government school — specifically the parent who wants
to help with their child's maths homework but left formal schooling before the
class their child is now in.

The system answers only from the child's own prescribed textbook: NCERT **गणित
मेला (Maths Mela)**, Class 5, aligned to NCF-2023 — fifteen chapters and 190
pages, obtained from ncert.nic.in. A parent asks a question in ordinary spoken
Hindi. The system locates the relevant passage in the textbook and returns four
things: a direct answer, the underlying rule, a worked example using different
numbers, and — most importantly — **a single sentence the parent can say aloud to
their child**.

That last element defines the product. The output is deliberately not an answer
to be copied into a notebook. It is a teaching prompt, so the parent can explain
rather than dictate. The guiding principle is stated as: *हम बच्चों के ट्यूटर नहीं
हैं — हम माता-पिता के कोच हैं* ("We are not a tutor for children; we are a coach
for parents").

### 2. Why voice is essential, not decorative

Two constraints make speech a functional requirement rather than a convenience.

First, **the target parent reads Devanagari slowly and with effort.** Many can
speak fluent Hindi while finding sustained reading tiring or embarrassing. An
answer they cannot comfortably read is an answer they cannot use. Text-to-speech
lets the guidance be *heard*, and heard repeatedly, before it is said to the
child.

Second, **typing Devanagari on a low-cost Android phone is a significant
barrier.** Parents who would readily send a voice note will often abandon a typed
question. Automatic speech recognition removes that barrier at the point where
users are otherwise lost.

There is also a safety dimension specific to mathematics. Numbers spoken aloud
are easily mis-heard — *"एक बटा चार"* (1/4) can be transcribed as *"एक बटा चालीस"*
(1/40), silently changing the question. The parent cannot detect this, because
they asked precisely because they did not know. The application therefore always
reads the transcript back and requires confirmation before answering. Speech
recognition quality and an explicit confirmation turn together make this safe.

### 3. Which Bhashini services are requested

- **ASR (Automatic Speech Recognition), Hindi** — converting a parent's spoken
  question, recorded as 16 kHz mono WAV in the browser, into Devanagari text.
- **TTS (Text-to-Speech), Hindi** — reading the generated four-part explanation
  back to the parent.

Both are accessed through the standard ULCA flow: a configuration call to
`getModelsPipeline`, followed by compute calls to the inference endpoint returned
by that response. The implementation deliberately reads the endpoint, inference
key and serviceId from the configuration response at runtime rather than
hard-coding published values, so that rotation on Bhashini's side does not break
the client.

Audio is short: single questions, capped at 60 seconds, and spoken answers of
roughly 60–90 words.

### 4. Why Bhashini specifically

This is a deliberate architectural decision, documented in the project's design
record, and it is the reason for this application rather than a commercial
alternative.

The content passing through the speech service is **a child's homework, spoken by
their parent, in their home.** Routing that through a foreign commercial speech
API means exporting the voices of rural Indian parents and details of their
children's education to infrastructure outside Indian jurisdiction, governed by
terms they cannot read and did not negotiate. For a product intended for
government-school families, that is not an acceptable default.

Bhashini is Indian public language infrastructure, built for exactly these
languages and exactly these users. Using it is consistent with the product's
stated positioning and with the digital-inclusion goals of NEP 2020 and the
Digital India programme.

A browser-based speech fallback is currently used for development. It is
explicitly labelled in the codebase as a stop-gap and *not* a privacy-preserving
option, because that fallback transmits audio to a foreign provider. Bhashini is
intended as the production path.

### 5. Expected usage volume

This is a prototype and, at present, a research and portfolio project. Volumes
are small and bounded:

- Development and evaluation: on the order of **50–200 ASR/TTS calls per day**.
- Any pilot would be limited to a single district and a small cohort of parents.

The application's answer-generation capacity is independently capped at
approximately **90 answers per day** by a separate free-tier limit, so speech
usage cannot meaningfully exceed that figure. No bulk, batch or offline
transcription is performed. No audio corpus is being assembled.

### 6. Data handling

- Audio is transmitted for immediate recognition and **is not stored** by the
  application. No transcripts are written to disk.
- No user accounts, no login, and no personally identifying information are
  collected.
- Conversation history is held only in memory for the duration of a session, and
  is deliberately not persisted, because accumulating transcripts of families'
  private conversations would be a data-governance decision made by accident.
- The textbook itself is never redistributed. Pages carry "© NCERT / not to be
  republished", so the deployed service fetches the relevant chapter from
  ncert.nic.in at request time and renders only the single page a parent asks to
  see.

### 7. Current status and evidence

The system is built, deployed and publicly reachable. Voice is the one remaining
component, and it is already implemented and awaiting credentials.

Measured results to date:

| Measure | Result |
|---|---|
| Textbook read accuracy | 0.7% character error rate; 100% numeric and fraction recall |
| Retrieval | correct passage returned 93% of the time |
| Answers correctly given | 87% of answerable questions |
| **Wrong-answer rate** | **1.1%** |
| Questions declined rather than guessed | 13% (design guardrail: 25%) |

The wrong-answer rate is the figure the design optimises against. A confidently
incorrect mathematical answer, taught by a parent to a child, is considerably
worse than an honest "I don't know" — so the system is explicitly built to refuse
when it cannot ground an answer in the textbook.

### 8. Declaration

This is a **non-commercial** project. It is not monetised, carries no
advertising, and sells no service. Access is requested under Bhashini's terms for
non-commercial use. I am happy to provide the source repository, the live
deployment, or further technical documentation on request.
