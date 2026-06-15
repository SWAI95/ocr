"""인식률 레버 측정 하네스 — doc_00 골드 라벨 기준 CER.

설정 매트릭스(엔진/DPI/det파라미터/rec모델)를 돌려 페이지별·평균 CER + 시간을
JSON 으로 출력. paddle CPU(5090)가 느리므로 프로브(일부 페이지)로 먼저 확인 가능.

사용:
    python scripts/measure_levers.py --preset probe
    python scripts/measure_levers.py --preset partA
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.imaging import decode_image  # noqa: E402
from backend.engines.base import EngineOptions  # noqa: E402
from backend.pipeline import runner  # noqa: E402
from backend.metrics.cer_wer import score  # noqa: E402

PDF = "samples/dataset/korean_docs/doc_00.pdf"
GT_DIR = Path("samples/dataset/korean_docs")
LIMIT = {"text_det_limit_side_len": 1536, "text_det_limit_type": "max"}
LIMIT2K = {"text_det_limit_side_len": 2048, "text_det_limit_type": "max"}

# (label, engine, dpi, opts_kw, extra, page_indices|None=전체)
PRESETS = {
    "probe": [
        ("easyocr 200dpi", "easyocr", 200, {}, {}, None),
        ("easyocr 300dpi", "easyocr", 300, {}, {}, None),
        ("paddle 200/기본", "paddle", 200, {"use_table": True}, {}, [1]),
        ("paddle 200/limit1536", "paddle", 200, {"use_table": True}, LIMIT, [1]),
        ("paddle 300/limit1536", "paddle", 300, {"use_table": True}, LIMIT, [1]),
    ],
    "partA": [
        ("paddle 200/기본", "paddle", 200, {"use_table": True}, {}, None),
        ("paddle 200/limit1536", "paddle", 200, {"use_table": True}, LIMIT, None),
        ("paddle 300/기본", "paddle", 300, {"use_table": True}, {}, None),
        ("paddle 300/limit1536", "paddle", 300, {"use_table": True}, LIMIT, None),
        ("paddle 300/limit2048", "paddle", 300, {"use_table": True}, LIMIT2K, None),
    ],
    "b2_rec": [  # rec 모델 비교 (Part A 최적 dpi/limit 가정)
        ("rec=korean_mobile", "paddle", 300, {"use_table": True}, LIMIT, None),
        ("rec=server", "paddle", 300, {"use_table": True, "rec_model": "PP-OCRv5_server_rec"}, LIMIT, None),
    ],
}

_pages_cache: dict[int, list] = {}


def get_pages(dpi: int):
    if dpi not in _pages_cache:
        _pages_cache[dpi] = decode_image(Path(PDF).read_bytes(), "doc_00.pdf", dpi=dpi)
    return _pages_cache[dpi]


def gts(n: int):
    return [(GT_DIR / f"doc_00_p{i}.gt.txt").read_text() for i in range(n)]


def run_config(label, engine, dpi, opts_kw, extra, idxs):
    pages = get_pages(dpi)
    gtl = gts(len(pages))
    idxs = list(range(len(pages))) if idxs is None else idxs
    per = {}
    sec = 0.0
    dev = ""
    for i in idxs:
        opts = EngineOptions(lang="korean", use_gpu=True, extra=dict(extra), **opts_kw)
        t0 = time.perf_counter()
        r = runner.run(engine, pages[i], opts)
        sec += time.perf_counter() - t0
        dev = r.device
        per[i] = round(score(gtl[i], r.full_text).cer * 100, 1)
    avg = round(sum(per.values()) / len(per), 2)
    return {"label": label, "dpi": dpi, "device": dev, "per_page": per,
            "avg_cer": avg, "sec": round(sec, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="probe", choices=list(PRESETS))
    args = ap.parse_args()
    out = []
    for cfg in PRESETS[args.preset]:
        res = run_config(*cfg)
        out.append(res)
        print(f"  {res['label']:24s} avg_CER {res['avg_cer']:>5}%  "
              f"pages={res['per_page']}  {res['device']} {res['sec']}s", flush=True)
    print("MEASURE_JSON:" + json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
