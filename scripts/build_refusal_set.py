"""Emit eval/refusal_set.json from the reviewable tuples in eval/refusal_set.py."""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
from refusal_set import ADVERSARIAL, IN_SYLLABUS, OUT_OF_SYLLABUS  # noqa: E402

out = []
for i, (q, ch, note) in enumerate(IN_SYLLABUS, 1):
    out.append({"id": f"rs-in-{i:03d}", "question_hi": q, "label": "answer",
                "subtype": "in_syllabus", "expected_chapter": ch, "note": note})
for i, (q, sub, note) in enumerate(OUT_OF_SYLLABUS, 1):
    out.append({"id": f"rs-out-{i:03d}", "question_hi": q, "label": "refuse",
                "subtype": sub, "expected_chapter": None, "note": note})
for i, (q, sub, note) in enumerate(ADVERSARIAL, 1):
    out.append({"id": f"rs-adv-{i:03d}", "question_hi": q, "label": "refuse",
                "subtype": sub, "expected_chapter": None, "note": note})

(ROOT / "eval" / "refusal_set.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
n_ans = sum(r["label"] == "answer" for r in out)
print(f"{len(out)} questions: {n_ans} answer / {len(out) - n_ans} refuse")
print(f"  in-syllabus {len(IN_SYLLABUS)}, out-of-syllabus {len(OUT_OF_SYLLABUS)}, adversarial {len(ADVERSARIAL)}")
from collections import Counter
print(f"  chapters covered: {sorted(Counter(c for _, c, _ in IN_SYLLABUS))}")
