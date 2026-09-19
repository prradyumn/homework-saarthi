# Share copy variants

`share-copy.txt` is the canonical caption. These are alternates.

## Short (X / Twitter)
Most AI demos brag about answering.
I built one whose best feature is saying "I don't know" — because its user is a
parent who can't tell when it's wrong.
1.1% wrong-answer rate. होमवर्क साथी →

## LinkedIn
Homework Saathi answers Class 5 maths questions in Hindi — for the *parent*, not
the child.

That distinction drove every engineering decision. The parent often left school
before the class their child is now in, so they cannot detect a wrong answer.
That makes fluency the failure mode, not the goal.

So it's built backwards from refusal: 516 out-of-syllabus chunks exist purely to
be matched against, so "is this Class 5 content?" becomes an answerable question.
87% of answerable questions get answered, at a 1.1% wrong-answer rate, and the
output isn't an answer at all — it's a sentence the parent says to their child.

Zero paid APIs. Live: homework-saathi.onrender.com

## Discord / dev-facing
RAG over one NCERT Class 5 maths textbook, in Hindi, for parents.
The interesting part isn't retrieval — it's the refusal gate. Similarity alone
gave 1% coverage; layered gating with out-of-syllabus decoys gave 87% at 1.1%
wrong. Whisper for voice in, on-device TTS out. Runs on free tiers.
