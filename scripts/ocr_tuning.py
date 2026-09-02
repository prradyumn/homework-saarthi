"""Sweep OCR inputs and configs against the ground-truth pages to lower prose CER.

Three hypotheses, all cheap to test:

1. The diagonal "© NCERT / not to be republished" watermark overlays the body
   text in the render. It is a light-grey fill, so binarising the page should
   erase it while leaving black text — and colour text does not matter, since
   headings are taken from the text layer anyway (D0.3).
2. Devanagari matras are small features; 300dpi may be under-resolved.
3. Tesseract's defaults (psm 3, oem 3) may not be right for a full textbook page.

Scoring reuses the bake-off's metrics so the numbers stay comparable.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

import pymupdf
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from textlayer_bakeoff import GT, ROOT, locate, score_page  # noqa: E402
from textnorm import normalize  # noqa: E402

RAW = ROOT / "ingest" / "raw"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="ocrtune-"))


def render(page: pymupdf.Page, dpi: int, binarise: int | None) -> pathlib.Path:
    out = TMP / f"{id(page)}-{dpi}-{binarise}.png"
    if out.exists():
        return out
    pix = page.get_pixmap(dpi=dpi)
    if binarise is None:
        pix.save(out)
        return out
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    # Anything lighter than the threshold becomes white: the grey watermark goes,
    # black body text stays.
    img = img.point(lambda v: 255 if v > binarise else 0, mode="L")
    img.save(out)
    return out


def tess(png: pathlib.Path, lang: str, psm: str, oem: str) -> str:
    proc = subprocess.run(
        ["tesseract", str(png), "stdout", "-l", lang, "--psm", psm, "--oem", oem],
        capture_output=True, text=True,
    )
    return proc.stdout


VARIANTS = [
    # label,                     lang,            psm, oem, dpi, binarise
    ("baseline hin 300 psm3",    "hin",           "3", "3", 300, None),
    ("hin 300 psm6",             "hin",           "6", "3", 300, None),
    ("hin 300 psm4",             "hin",           "4", "3", 300, None),
    ("hin 300 oem1(LSTM)",       "hin",           "3", "1", 300, None),
    ("hin 400",                  "hin",           "3", "3", 400, None),
    ("hin 600",                  "hin",           "3", "3", 600, None),
    ("hin 300 binarised@170",    "hin",           "3", "3", 300, 170),
    ("hin 300 binarised@200",    "hin",           "3", "3", 300, 200),
    ("hin 400 binarised@170",    "hin",           "3", "3", 400, 170),
    ("hin 600 binarised@170",    "hin",           "3", "3", 600, 170),
    ("Devanagari 300",           "Devanagari",    "3", "3", 300, None),
    ("hin+Devanagari 400 bin",   "hin+Devanagari", "3", "3", 400, 170),
]


def main() -> int:
    manifest = json.loads((ROOT / "ingest" / "manifest.json").read_text(encoding="utf-8"))
    pages = json.loads(GT.read_text(encoding="utf-8"))
    docs = {}

    print(f"{'variant':30}{'prose CER':>11}{'numeric':>10}")
    print("-" * 51)

    # reference point: the text layer with the deterministic normaliser applied
    cers, nrs = [], []
    for gt in pages:
        code, idx = locate(gt["book_page"], manifest)
        doc = docs.setdefault(code, pymupdf.open(RAW / f"{code}.pdf"))
        s = score_page(gt, normalize(doc[idx].get_text()))
        cers.append(s["cer"])
        nrs.append((s["numeric_recall"], s["numbers_total"]))
    print(
        f"{'textlayer + normaliser':30}{sum(cers) / len(cers):>10.1%}"
        f"{sum(v * w for v, w in nrs) / sum(w for _, w in nrs):>10.1%}"
    )

    results = {}
    for label, lang, psm, oem, dpi, binarise in VARIANTS:
        cers, nrs = [], []
        for gt in pages:
            code, idx = locate(gt["book_page"], manifest)
            doc = docs.setdefault(code, pymupdf.open(RAW / f"{code}.pdf"))
            png = render(doc[idx], dpi, binarise)
            s = score_page(gt, tess(png, lang, psm, oem))
            cers.append(s["cer"])
            nrs.append((s["numeric_recall"], s["numbers_total"]))
        cer = sum(cers) / len(cers)
        nr = sum(v * w for v, w in nrs) / sum(w for _, w in nrs)
        results[label] = {"cer": cer, "numeric": nr, "per_page": cers}
        print(f"{label:30}{cer:>10.1%}{nr:>10.1%}")

    best = min(results, key=lambda k: results[k]["cer"])
    print(f"\nbest prose CER: {best} at {results[best]['cer']:.1%}")
    print(f"  per-page: {[f'{c:.1%}' for c in results[best]['per_page']]}")
    (ROOT / "eval" / "ocr_tuning.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
