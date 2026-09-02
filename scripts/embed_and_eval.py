"""Embed chunks with BGE-M3 locally and measure retrieval against the seed set.

Runs against a local index (numpy) rather than pgvector, so the Step 1 exit
criterion — the right chapter for 90% of golden-set questions — can be tested
before any account exists. Swapping in Supabase later changes where the vectors
live, not whether retrieval works.

Also measures what BGE-M3 actually costs on this machine (8 GB M1), because the
query-time embedding has to fit alongside everything else at request time.

Usage:
  python scripts/embed_and_eval.py --embed     # build the index (slow, once)
  python scripts/embed_and_eval.py             # evaluate using the cached index
"""

from __future__ import annotations

import argparse
import json
import pathlib
import resource
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHUNKS = ROOT / "ingest" / "chunks.json"
GOLDEN = ROOT / "eval" / "golden_seed.json"
INDEX = ROOT / "ingest" / "index_bge_m3.npz"
REPORT = ROOT / "eval" / "retrieval_report.json"

MODEL = "BAAI/bge-m3"
TOP_K = 5


def peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024


def embedding_text(chunk: dict) -> str:
    """What actually gets embedded.

    The section header is prepended because it names the concept far more
    reliably than the body prose, which wanders through story context (a dairy
    farm, a coconut grove). Fractions are appended as tokens so that a question
    about 1/2 can match a chunk where the fraction only exists as page geometry.
    """
    parts = [
        f"अध्याय {chunk['chapter']}: {chunk['chapter_title_hi']}",
        chunk["section_header_hi"],
        chunk["text_hi"],
    ]
    if chunk.get("fractions"):
        parts.append(" ".join(chunk["fractions"]))
    return "\n".join(p for p in parts if p)


def load_model():
    from sentence_transformers import SentenceTransformer

    t0 = time.time()
    model = SentenceTransformer(MODEL, device="cpu")
    print(f"  loaded {MODEL} in {time.time() - t0:.0f}s, peak RSS {peak_rss_mb():.0f} MB")
    return model


def build_index() -> None:
    chunks = json.loads(CHUNKS.read_text(encoding="utf-8"))
    model = load_model()
    texts = [embedding_text(c) for c in chunks]

    t0 = time.time()
    vecs = model.encode(
        texts, batch_size=4, normalize_embeddings=True,
        show_progress_bar=True, convert_to_numpy=True,
    )
    elapsed = time.time() - t0
    np.savez_compressed(
        INDEX, vectors=vecs.astype(np.float32), ids=np.array([c["id"] for c in chunks])
    )
    print(
        f"\n  embedded {len(texts)} chunks in {elapsed:.0f}s "
        f"({elapsed / len(texts):.2f}s/chunk), dim={vecs.shape[1]}"
    )
    print(f"  peak RSS {peak_rss_mb():.0f} MB | index {INDEX.stat().st_size / 1e6:.1f} MB")


def evaluate() -> int:
    chunks = json.loads(CHUNKS.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in chunks}
    data = np.load(INDEX, allow_pickle=False)
    vectors, ids = data["vectors"], [str(x) for x in data["ids"]]

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["questions"]
    model = load_model()

    t0 = time.time()
    qvecs = model.encode(
        [q["question_hi"] for q in golden], batch_size=4,
        normalize_embeddings=True, convert_to_numpy=True,
    )
    q_time = (time.time() - t0) / len(golden)

    rows, hit1 = [], 0
    hit_topk = 0
    page_close = 0
    for q, qv in zip(golden, qvecs):
        sims = vectors @ qv
        order = np.argsort(-sims)[:TOP_K]
        top = [by_id[ids[i]] for i in order]
        scores = [float(sims[i]) for i in order]

        top1_ok = top[0]["chapter"] == q["chapter"]
        topk_ok = any(c["chapter"] == q["chapter"] for c in top)
        near = any(
            c["chapter"] == q["chapter"] and abs(c["page"] - q["page"]) <= 2 for c in top
        )
        hit1 += top1_ok
        hit_topk += topk_ok
        page_close += near

        rows.append(
            {
                "id": q["id"], "question_hi": q["question_hi"],
                "expected_chapter": q["chapter"], "expected_page": q["page"],
                "expected_tag": q["concept_tag"],
                "top1_chapter": top[0]["chapter"], "top1_page": top[0]["page"],
                "top1_tag": top[0]["concept_tag"],
                "top1_header": top[0]["section_header_hi"],
                "top1_score": round(scores[0], 4),
                "margin_top1_top5": round(scores[0] - scores[-1], 4),
                "chapter_hit_top1": top1_ok,
                "chapter_hit_topk": topk_ok,
                "page_within_2": near,
                "topk": [
                    {"chapter": c["chapter"], "page": c["page"],
                     "header": c["section_header_hi"], "score": round(s, 4)}
                    for c, s in zip(top, scores)
                ],
            }
        )

    n = len(golden)
    print(f"\n  {'id':8}{'exp':>5}{'got':>5}  {'score':>7}{'margin':>8}  header")
    print("  " + "-" * 74)
    for r in rows:
        mark = " " if r["chapter_hit_top1"] else "X"
        print(
            f"{mark} {r['id']:8}{r['expected_chapter']:>5}{r['top1_chapter']:>5}  "
            f"{r['top1_score']:>7.3f}{r['margin_top1_top5']:>8.3f}  "
            f"{r['top1_header'][:38]}"
        )

    print(f"\n  chapter hit @1   {hit1}/{n} = {hit1 / n:.0%}")
    print(f"  chapter hit @{TOP_K}   {hit_topk}/{n} = {hit_topk / n:.0%}   <- exit criterion: 90%")
    print(f"  page within +/-2 {page_close}/{n} = {page_close / n:.0%}")
    print(f"  query embed time {q_time:.2f}s | peak RSS {peak_rss_mb():.0f} MB")

    REPORT.write_text(
        json.dumps(
            {
                "model": MODEL, "top_k": TOP_K, "chunks": len(chunks), "questions": n,
                "chapter_hit_at_1": hit1 / n, "chapter_hit_at_k": hit_topk / n,
                "page_within_2": page_close / n,
                "query_embed_seconds": round(q_time, 3),
                "peak_rss_mb": round(peak_rss_mb()),
                "results": rows,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  report -> {REPORT}")
    return 0 if hit_topk / n >= 0.9 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed", action="store_true", help="build the index first")
    args = ap.parse_args()
    if args.embed or not INDEX.exists():
        build_index()
    return evaluate()


if __name__ == "__main__":
    sys.exit(main())
