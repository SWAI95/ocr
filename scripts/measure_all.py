"""측정 하네스 — 보고서/PPTX용 성능 데이터를 data/measurements.json 에 저장.

phase:
  gpu  : 전 엔진 × doc_00/04/05 (GPU). 문서-micro CER/WER + 시간.
  cpu  : 전 엔진 × doc_00 (CPU 강제). 시간 비교용.
  seal : red_stamp on/off × {hybrid_vl,paddle,paddle_vl} × {doc_00,doc_04} (GPU).
사용: OCR_PADDLE_GPU=force ./.venv/bin/python scripts/measure_all.py gpu

문서-micro: 페이지 출력/GT 를 이어붙여 한 번에 채점(웹 /api 와 동일). release_all 로
엔진 전환 간 VRAM 반환 → VLM 의 Ollama CPU 폴백(경합) 방지.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.engines.base import EngineOptions  # noqa: E402
from backend.imaging import decode_image  # noqa: E402
from backend.metrics.cer_wer import score  # noqa: E402
from backend.pipeline import runner  # noqa: E402

GTD = Path("samples/dataset/korean_docs")
OUTJSON = Path("data/measurements.json")
DOCS = {"doc_00": "doc_00.pdf", "doc_04": "doc_04.pdf", "doc_05": "doc_05.pdf"}
ENGINES = ["paddle", "paddle_vl", "hybrid_vl", "ollama_vl", "easyocr", "tesseract"]
# 엔진별 격리 실행용: MEAS_ENGINES=paddle_vl 처럼 주면 그 엔진만(한 프로세스=fresh GPU
# 컨텍스트). paddle(PPStructureV3)을 먼저 로드한 같은 프로세스에서 paddle_vl 을 돌리면
# VLM 생성이 CPU 로 떨어지는 문제가 있어, GPU 측정은 엔진마다 별도 프로세스로 돌린다.
_FILTER = [e.strip() for e in os.environ.get("MEAS_ENGINES", "").split(",") if e.strip()]
if _FILTER:
    ENGINES = [e for e in ENGINES if e in _FILTER]


def load_json() -> dict:
    if OUTJSON.exists():
        return json.loads(OUTJSON.read_text())
    return {}


def save_json(d: dict) -> None:
    OUTJSON.parent.mkdir(parents=True, exist_ok=True)
    OUTJSON.write_text(json.dumps(d, ensure_ascii=False, indent=2))


def doc_pages(stem: str):
    p = GTD / DOCS[stem]
    return decode_image(p.read_bytes(), p.name)


def doc_gt(stem: str, npages: int) -> str:
    parts = []
    for i in range(npages):
        f = GTD / f"{stem}_p{i}.gt.txt"
        if f.exists():
            parts.append(f.read_text())
    return "\n".join(parts)


def run_doc(engine: str, stem: str, use_gpu: bool, pp_steps=()) -> dict | None:
    if not runner.is_available(engine):
        print(f"    {engine}/{stem}: 비가용"); return None
    imgs = doc_pages(stem)
    opts = EngineOptions(lang="korean", use_gpu=use_gpu, use_layout=True, use_table=True)
    texts, devs = [], set()
    t0 = time.perf_counter()
    for img in imgs:
        if pp_steps:
            from backend.preprocess import preprocess as _pp
            img = _pp(img, list(pp_steps))[0]
        r = runner.run(engine, img, opts)
        texts.append(r.full_text); devs.add(r.device)
    wall = time.perf_counter() - t0
    sc = score(doc_gt(stem, len(imgs)), "\n".join(texts))
    return {"cer": round(sc.cer * 100, 2), "wer": round(sc.wer * 100, 2),
            "total_s": round(wall, 1), "per_page_s": round(wall / len(imgs), 1),
            "pages": len(imgs), "device": "/".join(sorted(devs))}


def phase_gpu(data: dict) -> None:
    data.setdefault("gpu", {})
    for eng in ENGINES:
        for stem in DOCS:
            r = run_doc(eng, stem, use_gpu=True)
            if r:
                data["gpu"].setdefault(stem, {})[eng] = r
                print(f"  GPU {eng:11s} {stem}  CER {r['cer']:5.2f}%  "
                      f"{r['total_s']:6.1f}s ({r['per_page_s']}s/p) {r['device']}")
                save_json(data)
        runner.release_all()


def phase_cpu(data: dict) -> None:
    data["cpu_doc00"] = data.get("cpu_doc00", {})
    for eng in ENGINES:
        r = run_doc(eng, "doc_00", use_gpu=False)
        if r:
            data["cpu_doc00"][eng] = r
            print(f"  CPU {eng:11s} doc_00  CER {r['cer']:5.2f}%  "
                  f"{r['total_s']:6.1f}s ({r['per_page_s']}s/p) {r['device']}")
            save_json(data)
        runner.release_all()


def phase_seal(data: dict) -> None:
    data.setdefault("seal", {})
    seal_engs = [e for e in ("hybrid_vl", "paddle", "paddle_vl")
                 if not _FILTER or e in _FILTER]
    for stem in ("doc_00", "doc_04"):
        data["seal"].setdefault(stem, {})
        for eng in seal_engs:
            off = run_doc(eng, stem, use_gpu=True)
            runner.release_all()
            on = run_doc(eng, stem, use_gpu=True, pp_steps=("red_stamp",))
            runner.release_all()
            if off and on:
                data["seal"][stem][eng] = {"off_cer": off["cer"], "on_cer": on["cer"],
                                           "off_wer": off["wer"], "on_wer": on["wer"]}
                print(f"  SEAL {stem} {eng:10s}  off {off['cer']:.2f}% → on {on['cer']:.2f}%  "
                      f"(Δ{on['cer']-off['cer']:+.2f})")
                save_json(data)


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    data = load_json()
    print(f"=== measure phase={phase} ===")
    if phase in ("gpu", "all"):
        phase_gpu(data)
    if phase in ("seal", "all"):
        phase_seal(data)
    if phase in ("cpu", "all"):
        phase_cpu(data)
    save_json(data)
    print(f"[OK] {OUTJSON}")


if __name__ == "__main__":
    main()
