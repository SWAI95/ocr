"""FastAPI 백엔드 — 모델 선택 / 문서 업로드 / 전체 페이지 OCR / CER·WER.

문서(이미지·PDF)를 받으면 **전체 페이지를 한 번에** OCR 하고, 페이지별 인식
결과를 이어붙여(full_text) 하나의 정답(ground_truth)과 비교한다. 정답도 .txt
파일 여러 개를 받아 이어붙인 것이므로 "이어붙인 정답 ↔ 이어붙인 인식결과"를
문서 단위로 한 번에 채점한다.

실행:
    .venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.engines.base import EngineOptions
from backend.imaging import decode_image, encode_png_b64, render_overlay
from backend.metrics.cer_wer import score
from backend.models.registry import list_engines
from backend.pipeline import runner

FRONTEND_DIR = config.PROJECT_ROOT / "frontend"

app = FastAPI(title="Offline Korean OCR Bench", version="0.2.0")


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/engines")
def api_engines():
    """엔진 카탈로그 + 설치 가용성."""
    engines = list_engines()
    for e in engines:
        e["available"] = runner.is_available(e["id"])
    return {"offline": config.OFFLINE, "engines": engines}


def _decode_all(data: bytes, filename: str):
    """업로드 바이트 → 전체 페이지 이미지 리스트(PDF는 페이지별)."""
    try:
        return decode_image(data, filename)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


def _apply_pp(image, pp_steps):
    """전처리 스텝 적용(없으면 원본 그대로). 오버레이도 전처리 이미지 기준."""
    if not pp_steps:
        return image
    from backend.preprocess import preprocess as _pp
    out, _ = _pp(image, pp_steps)
    return out


def _page_payload(idx: int, image, result, include_overlay: bool = True) -> dict:
    """페이지 1장의 인식 결과 → 표준 dict(시각화/텍스트용)."""
    payload = {
        "page": idx,
        "num_lines": len(result.lines),
        "num_regions": len(result.regions),
        "lines": [
            {"text": l.text, "score": round(l.score, 4),
             "polygon": l.polygon, "region": l.region_label}
            for l in result.lines
        ],
        "regions": [
            {"label": r.label, "score": round(r.score, 4),
             "bbox": r.bbox.as_list(), "raw": r.raw_label}
            for r in result.regions
        ],
        "tables": [
            {"bbox": t.bbox.as_list(), "html": t.html} for t in result.tables
        ],
        "timings_ms": result.timings_ms,
        "total_ms": round(sum(result.timings_ms.values()), 1),
    }
    if include_overlay:
        payload["overlay"] = encode_png_b64(
            render_overlay(image, result.lines, result.regions))
    return payload


def _run_all(images, engine: str, opts: EngineOptions, ground_truth: str,
             pp_steps=(), include_overlay: bool = True) -> dict:
    """엔진 1개로 전체 페이지를 OCR → 페이지별 결과 + 전체 이어붙인 텍스트/지표.

    run/compare 공용. CER/WER 은 페이지를 이어붙인 full_text 와 정답을 한 번에
    비교해 문서 단위 1개만 낸다.
    """
    pages = []
    full_parts = []
    total_timings: dict[str, float] = {}
    device = "cpu"
    meta: dict = {}
    for idx, image in enumerate(images):
        image = _apply_pp(image, pp_steps)
        result = runner.run(engine, image, opts)
        device = result.device
        meta = result.meta
        for k, v in result.timings_ms.items():
            total_timings[k] = total_timings.get(k, 0.0) + v
        full_parts.append(result.full_text)
        pages.append(_page_payload(idx, image, result, include_overlay))
    full_text = "\n".join(full_parts)
    return {
        "engine": engine,
        "device": device,
        "meta": meta,
        "num_pages": len(images),
        "timings_ms": total_timings,
        "total_ms": round(sum(total_timings.values()), 1),
        "full_text": full_text,
        "num_lines": sum(p["num_lines"] for p in pages),
        "num_regions": sum(p["num_regions"] for p in pages),
        "metrics": score(ground_truth, full_text).to_dict()
                   if ground_truth.strip() else None,
        "pages": pages,
    }


@app.post("/api/run")
async def api_run(
    file: UploadFile = File(...),
    engine: str = Form("hybrid_vl"),
    lang: str = Form("korean"),
    det_model: Optional[str] = Form(None),
    rec_model: Optional[str] = Form(None),
    layout_model: Optional[str] = Form(None),
    use_layout: bool = Form(True),
    use_table: bool = Form(False),
    use_seal: bool = Form(False),
    use_gpu: bool = Form(True),
    ground_truth: str = Form(""),
    preprocess: str = Form(""),
):
    """엔진 1개로 업로드 문서의 전체 페이지를 OCR."""
    if not runner.is_available(engine):
        raise HTTPException(400, f"엔진 '{engine}' 미설치 또는 비활성")
    data = await file.read()
    images = _decode_all(data, file.filename or "")
    pp_steps = [s.strip() for s in preprocess.split(",") if s.strip()]
    opts = EngineOptions(
        lang=lang, use_gpu=use_gpu, use_layout=use_layout,
        use_table=use_table, use_seal=use_seal,
        det_model=det_model or None, rec_model=rec_model or None,
        layout_model=layout_model or None,
    )
    try:
        out = _run_all(images, engine, opts, ground_truth, pp_steps)
    except Exception as e:  # noqa: BLE001
        import traceback
        raise HTTPException(500, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}")
    return JSONResponse(out)


@app.post("/api/compare")
async def api_compare(
    file: UploadFile = File(...),
    engines: str = Form("paddle,easyocr"),
    lang: str = Form("korean"),
    use_layout: bool = Form(True),
    use_table: bool = Form(False),
    use_gpu: bool = Form(True),
    ground_truth: str = Form(""),
):
    """같은 문서(전체 페이지)를 여러 엔진으로 실행해 CER/WER·속도를 나란히 비교."""
    data = await file.read()
    images = _decode_all(data, file.filename or "")
    engine_ids = [e.strip() for e in engines.split(",") if e.strip()]

    results = []
    for eid in engine_ids:
        if not runner.is_available(eid):
            results.append({"engine": eid, "error": "미설치/비활성"})
            continue
        opts = EngineOptions(lang=lang, use_gpu=use_gpu,
                             use_layout=use_layout, use_table=use_table)
        try:
            results.append(_run_all(images, eid, opts, ground_truth))
        except Exception as e:  # noqa: BLE001
            results.append({"engine": eid, "error": f"{type(e).__name__}: {e}"})

    return JSONResponse({"num_pages": len(images), "results": results})


# 정적 파일 (app.js, style.css)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
