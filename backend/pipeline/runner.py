"""엔진 레지스트리 + 실행 오케스트레이션.

엔진 인스턴스는 지연 생성(모델 로드는 최초 호출 시) 후 싱글톤 캐시.
설치되지 않은 엔진은 available=False 로 표시되어 웹에서 비활성.
"""
from __future__ import annotations

import importlib
import importlib.util

from backend.engines.base import EngineOptions, OCRResult, OCREngine

# 엔진 id → (모듈경로, 클래스명, import 가능 판단용 패키지)
_ENGINE_SPECS = {
    "paddle":    ("backend.engines.paddle_engine", "PaddleEngine", "paddleocr"),
    "paddle_vl": ("backend.engines.paddle_vl_engine", "PaddleVLEngine", "paddleocr"),
    "ollama_vl": ("backend.engines.ollama_vl_engine", "OllamaVLEngine", "cv2"),
    "hybrid_vl": ("backend.engines.hybrid_vl_engine", "HybridVLEngine", "paddleocr"),
    "easyocr":   ("backend.engines.easyocr_engine", "EasyOCREngine", "easyocr"),
    "tesseract": ("backend.engines.tesseract_engine", "TesseractEngine", "pytesseract"),
}

_instances: dict[str, OCREngine] = {}


def is_available(engine_id: str) -> bool:
    spec = _ENGINE_SPECS.get(engine_id)
    if not spec:
        return False
    _, _, pkg = spec
    try:
        if importlib.util.find_spec(pkg) is None:
            return False
    except Exception:
        return False
    # tesseract: pip(pytesseract) 만으론 부족 — 시스템 바이너리까지 있어야 동작.
    if engine_id == "tesseract":
        import shutil
        return shutil.which("tesseract") is not None
    # paddle_vl 은 VLM 의 fused 커널(fuse_rms_norm / sparse flash-attn)이 sm_120
    # (Blackwell) 미지원 → 5090 에선 인식이 빈 출력. 그 환경에선 비가용 처리(웹 비교
    # 오염 방지). 4090 등 비-Blackwell 은 native GPU 로 정상. OCR_PADDLE_VL_FORCE=1 로 강제.
    if engine_id == "paddle_vl":
        import os
        if os.environ.get("OCR_PADDLE_VL_FORCE", "0").lower() in ("1", "on", "true"):
            return True
        try:
            from backend.engines.paddle_engine import _is_blackwell_gpu
            return not _is_blackwell_gpu()
        except Exception:
            return True
    return True


def get_engine(engine_id: str) -> OCREngine:
    if engine_id not in _ENGINE_SPECS:
        raise KeyError(f"알 수 없는 엔진: {engine_id}")
    if engine_id not in _instances:
        mod_path, cls_name, _ = _ENGINE_SPECS[engine_id]
        mod = importlib.import_module(mod_path)
        _instances[engine_id] = getattr(mod, cls_name)()
    return _instances[engine_id]


def run(engine_id: str, image, opts: EngineOptions) -> OCRResult:
    return get_engine(engine_id).run(image, opts)
