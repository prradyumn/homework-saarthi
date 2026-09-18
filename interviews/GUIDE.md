# Parent discovery interviews — the instrument

**This is the study the whole thesis is gated on.** Everything measured so far
assumes one unverified thing: *that a parent who is told "I don't know" stays,
rather than leaving and never coming back.* If that is false, the refusal gate —
the part of this product with the most engineering in it — is a liability, and
the product should be re-scoped to answer-verification instead.

Two questions decide it. They are pre-registered below, with their decision rules
fixed **before** any data is collected, so a disappointing result cannot be
reasoned away afterwards.

---

## 0. Who counts as a participant

All four must be true. Do not stretch these to fill a slot; a wrong participant
is worse than a missing one, because their answers get averaged in anyway.

- [ ] Parent or primary guardian of a child **currently in Class 5**
- [ ] Child attends a **government school**
- [ ] The parent **has been asked for homework help by this child** in the last month
- [ ] Speaks Hindi as their main language at home

**Target: 8 completed interviews.** The kill criterion is defined on 8 (§3).

Deliberately *not* screened out: parents who cannot read Devanagari fluently.
They are the core user (§3.1). If anything, over-sample them.

---

## 1. Consent — read this aloud, in Hindi, before anything else

> नमस्ते। मैं एक ऐप बना रहा हूँ जो माता-पिता को अपने बच्चे को पढ़ाने में मदद करे।
> मैं आपको कुछ दिखाना चाहता हूँ और आपकी राय जाननी है।
>
> इसमें कोई सही या गलत जवाब नहीं है। मैं आपकी या आपके बच्चे की परीक्षा नहीं ले रहा।
> अगर कोई सवाल अच्छा न लगे तो मत बताइए, और कभी भी रोक सकते हैं।
>
> क्या मैं नोट्स लिख सकता हूँ? आपका नाम कहीं नहीं लिखा जाएगा।

Do not record audio unless they say yes unprompted. Notes are enough, and a
recorder changes how people answer a question about their own schooling.

**Never say** you built it, that it is "AI", or that it is clever. Every one of
those buys you politeness instead of data.

---

## 2. Warm-up — what actually happens (10 min)

Behaviour first, opinions later. People are reliable about last Tuesday and
unreliable about hypotheticals.

1. आखिरी बार आपके बच्चे ने होमवर्क में मदद कब माँगी? क्या हुआ था?
2. उस वक़्त आपने क्या किया? *(probe: किसी से पूछा? फ़ोन देखा? छोड़ दिया?)*
3. गणित में सबसे ज़्यादा दिक्कत कब आती है?
4. जब आपको खुद जवाब नहीं पता होता, तब आप क्या करते हैं?

*Listen for, do not ask about:* whether they hand the child an answer or try to
explain; whether anyone else in the house is the "maths person"; whether they
have ever felt embarrassed. Write down their **exact words** for what they do
when stuck — that vocabulary is what `retrieval.PARENT_TO_BOOK` exists to map.

---

## 3. THE CARD TEST — pre-registered, decision-bearing

Two cards, printed, same size, same typeface, no branding. Hand them over
**physically**. Randomise which card is on top per participant and record it.

**Card A — the answer**

> **1 किलोग्राम में 1000 ग्राम होते हैं।**

**Card B — how to explain it**

> **आपके बच्चे को समझाने का तरीका:**
> किलोग्राम को ग्राम में बदलने के लिए 1000 से गुणा कीजिए।
> जैसे 2 किलो = 2000 ग्राम।
>
> **बच्चे से यह कहिए:** "बेटा, एक किलो में हज़ार ग्राम होते हैं।"

Ask, in this order, and do not editorialise between them:

1. इन दोनों में से कौन सा आपके लिए ज़्यादा काम का है? **(record A or B)**
2. क्यों?
3. अगर रोज़ रात को एक ही मिल सकता, तो कौन सा चुनेंगे? **(record A or B)**

> ### Decision rule — fixed in advance
>
> **If fewer than 5 of 8 choose Card B on Q3 → the positioning is wrong.**
> Re-scope from *coaching the parent* to *answer-verification*, and the refusal
> gate stops being the centre of the product.
>
> 5 or more → the thesis holds and the current build is on the right track.
>
> This rule was written before the first interview. Do not move it afterwards.

---

## 4. Q11 — THE question the thesis rests on (15 min)

The trap here is politeness. Asked "would you mind if it said I don't know?",
almost everyone says they would not mind, and the answer is worthless. So it is
asked three ways: behavioural, projective, and revealed.

**4a. Behavioural — show, don't describe.** Open the live demo on your phone
(<https://homework-saathi.onrender.com>) and hand it to them. Ask them to type or
say a question about their child's maths. If it answers, fine. Then ask them to
try: *"द्विघात समीकरण का सूत्र क्या है?"* — which it refuses.

Then, watching their face rather than prompting:

- अभी क्या हुआ? *(do they understand it declined, or think it broke?)*
- अब आप क्या करेंगे? **(record verbatim)**

**4b. Projective — ask about someone else.** People admit about a neighbour what
they will not admit about themselves.

- मान लीजिए आपकी पड़ोसन ये इस्तेमाल कर रही हैं और ये कहता है "मुझे नहीं पता"।
  वो क्या सोचेंगी?
- क्या वो दोबारा इस्तेमाल करेंगी?

**4c. Revealed — make it cost something.** A preference with no price attached is
not a preference.

- अगर ये दस में से नौ बार सही बताए और एक बार कहे "मुझे नहीं पता" —
  क्या आप इस्तेमाल करेंगी?
- और अगर ये **हर बार** कुछ न कुछ बता दे, पर कभी-कभी गलत हो — तब?
- इन दोनों में से कौन सा बेहतर है? **(record: honest / always-answers)**

> ### Decision rule — fixed in advance
>
> **4c is the one that counts.** If fewer than 6 of 8 prefer *honest* over
> *always-answers*, the refusal gate is not the asset it is assumed to be, and
> §8.1's whole design needs revisiting.
>
> 4a and 4b are diagnostic: they tell you *why*, and whether a refusal reads as
> "broken" rather than "honest" — which would be a copy problem, not a thesis
> problem, and is fixable.

---

## 5. Close (5 min)

- अगर ये कल से बंद हो जाए, तो आपको फ़र्क पड़ेगा? क्यों?
- किस चीज़ के लिए आप इसे किसी और को बताएँगी?
- कुछ और जो मैंने नहीं पूछा?

Then: thank them, and **do not** pitch or ask for a referral in the same breath.

---

## 6. Within 30 minutes of finishing

Memory decays fast and in a flattering direction. Capture before you do anything
else:

```bash
python interviews/capture.py            # walks this guide, writes a session file
python interviews/analyse.py            # applies the rules above to everything so far
```

`analyse.py` refuses to declare a verdict until all 8 are in, so a promising
run of 3 cannot be mistaken for a result.

---

## 7. Things that will ruin this study

1. **Explaining the product before the card test.** They will pick the card that
   matches whatever you just described. Show the cards cold.
2. **Asking "would you like…"** Anything phrased that way gets a yes. Ask what
   they *did*, or what a neighbour *would* do.
3. **Interviewing people who can help with Class 5 maths themselves.** They are
   not the user, and they will tell you the product is unnecessary — correctly,
   for them.
4. **Stopping early because the first three agree with you.** The rule is 8.
5. **Moving the decision rule after seeing the data.** If that becomes tempting,
   the honest move is to say the study failed and design a better one.
