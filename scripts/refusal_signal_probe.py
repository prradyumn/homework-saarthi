"""Probe whether retrieval similarity alone can separate in-syllabus questions
from out-of-syllabus ones — the signal PRD §8.1's refusal gate rests on.

Result: it CANNOT. Lowest in-syllabus score 0.496 (a real question about the
tangram) sits BELOW the highest out-of-syllabus score 0.569 (Class 9 algebra,
"x^2 + 5x + 6 का गुणनखंड कैसे निकालें?"). Any single threshold either answers the
algebra question or refuses legitimate questions about tangrams, right angles and
quilt patterns.

This is an early, empirical vindication of §8.1 specifying FOUR signals rather
than one — and it exposes a gap in signal 3. See DECISIONS.md D1-PRELIM.
"""
import json, pathlib, sys, numpy as np
sys.path.insert(0, "scripts")
from sentence_transformers import SentenceTransformer

chunks = json.loads(pathlib.Path("ingest/chunks.json").read_text(encoding="utf-8"))
d = np.load("ingest/index_bge_m3.npz", allow_pickle=False)
vecs, ids = d["vectors"], [str(x) for x in d["ids"]]
by_id = {c["id"]: c for c in chunks}

IN = [q["question_hi"] for q in json.loads(pathlib.Path("eval/golden_seed.json").read_text(encoding="utf-8"))["questions"]]
OUT = [
    ("x^2 + 5x + 6 का गुणनखंड कैसे निकालें?", "class 9 algebra"),
    ("साइन थीटा और कॉस थीटा में क्या संबंध है?", "trigonometry"),
    ("भारत के प्रधानमंत्री कौन हैं?", "general knowledge"),
    ("प्रकाश संश्लेषण की प्रक्रिया समझाइए", "class 7 science"),
    ("वर्गमूल निकालने की विधि बताइए", "class 8 maths"),
    ("मेरा फोन गरम हो रहा है क्या करूँ?", "off-topic"),
    ("आज मौसम कैसा रहेगा?", "off-topic"),
    ("द्विघात समीकरण का सूत्र क्या है?", "class 10 algebra"),
]
model = SentenceTransformer("BAAI/bge-m3", device="cpu")
def top(qs):
    qv = model.encode(qs, batch_size=4, normalize_embeddings=True, convert_to_numpy=True)
    out = []
    for q, v in zip(qs, qv):
        s = vecs @ v
        o = np.argsort(-s)[:5]
        out.append((q, float(s[o[0]]), float(s[o[0]]-s[o[-1]]), by_id[ids[o[0]]]))
    return out

print(f"{'IN-SYLLABUS (30)':40} top1   margin")
ins = top(IN)
for q, s, m, c in sorted(ins, key=lambda r: r[1])[:4]:
    print(f"  lowest: {q[:44]:46} {s:.3f}  {m:.3f}")
i_scores = [s for _, s, _, _ in ins]
print(f"  min={min(i_scores):.3f} mean={sum(i_scores)/len(i_scores):.3f} max={max(i_scores):.3f}")

print(f"\n{'OUT-OF-SYLLABUS / OFF-TOPIC (8)':40} top1   margin   what it matched")
outs = top([q for q, _ in OUT])
for (q, kind), (_, s, m, c) in zip(OUT, outs):
    print(f"  {kind:18} {s:.3f}  {m:.3f}   ch{c['chapter']} {c['concept_tag']}")
o_scores = [s for _, s, _, _ in outs]
print(f"  min={min(o_scores):.3f} mean={sum(o_scores)/len(o_scores):.3f} max={max(o_scores):.3f}")

print(f"\nSEPARATION: lowest in-syllabus {min(i_scores):.3f}  vs  highest out-of-syllabus {max(o_scores):.3f}")
print(f"  -> {'OVERLAP: a single similarity threshold cannot separate them' if max(o_scores) >= min(i_scores) else 'CLEAN GAP: a threshold exists'}")
import json, pathlib, sys, numpy as np
sys.path.insert(0, "scripts")
from sentence_transformers import SentenceTransformer

chunks = json.loads(pathlib.Path("ingest/chunks.json").read_text(encoding="utf-8"))
d = np.load("ingest/index_bge_m3.npz", allow_pickle=False)
vecs, ids = d["vectors"], [str(x) for x in d["ids"]]
by_id = {c["id"]: c for c in chunks}

IN = [q["question_hi"] for q in json.loads(pathlib.Path("eval/golden_seed.json").read_text(encoding="utf-8"))["questions"]]
OUT = [
    ("x^2 + 5x + 6 का गुणनखंड कैसे निकालें?", "class 9 algebra"),
    ("साइन थीटा और कॉस थीटा में क्या संबंध है?", "trigonometry"),
    ("भारत के प्रधानमंत्री कौन हैं?", "general knowledge"),
    ("प्रकाश संश्लेषण की प्रक्रिया समझाइए", "class 7 science"),
    ("वर्गमूल निकालने की विधि बताइए", "class 8 maths"),
    ("मेरा फोन गरम हो रहा है क्या करूँ?", "off-topic"),
    ("आज मौसम कैसा रहेगा?", "off-topic"),
    ("द्विघात समीकरण का सूत्र क्या है?", "class 10 algebra"),
]
model = SentenceTransformer("BAAI/bge-m3", device="cpu")
def top(qs):
    qv = model.encode(qs, batch_size=4, normalize_embeddings=True, convert_to_numpy=True)
    out = []
    for q, v in zip(qs, qv):
        s = vecs @ v
        o = np.argsort(-s)[:5]
        out.append((q, float(s[o[0]]), float(s[o[0]]-s[o[-1]]), by_id[ids[o[0]]]))
    return out

print(f"{'IN-SYLLABUS (30)':40} top1   margin")
ins = top(IN)
for q, s, m, c in sorted(ins, key=lambda r: r[1])[:4]:
    print(f"  lowest: {q[:44]:46} {s:.3f}  {m:.3f}")
i_scores = [s for _, s, _, _ in ins]
print(f"  min={min(i_scores):.3f} mean={sum(i_scores)/len(i_scores):.3f} max={max(i_scores):.3f}")

print(f"\n{'OUT-OF-SYLLABUS / OFF-TOPIC (8)':40} top1   margin   what it matched")
outs = top([q for q, _ in OUT])
for (q, kind), (_, s, m, c) in zip(OUT, outs):
    print(f"  {kind:18} {s:.3f}  {m:.3f}   ch{c['chapter']} {c['concept_tag']}")
o_scores = [s for _, s, _, _ in outs]
print(f"  min={min(o_scores):.3f} mean={sum(o_scores)/len(o_scores):.3f} max={max(o_scores):.3f}")

print(f"\nSEPARATION: lowest in-syllabus {min(i_scores):.3f}  vs  highest out-of-syllabus {max(o_scores):.3f}")
print(f"  -> {'OVERLAP: a single similarity threshold cannot separate them' if max(o_scores) >= min(i_scores) else 'CLEAN GAP: a threshold exists'}")
