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
    # paddle_vl: cu129 휠 + markdown 접근자 수정 후 Blackwell(5090) GPU 도 정상
    # 동작 확인(실측). 과거의 Blackwell 비가용 처리는 제거 — paddleocr 만 깔려 있으면
    # 가용. (CPU 강제는 OCR_PADDLE_VL_GPU=off.)
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
    """엔진 실행 + 결정론적 후처리 정규화(W→₩ 등)를 최종 출력에 1회 적용.

    hybrid_vl 내부의 paddle/ollama 호출은 engine.run() 을 직접 거치므로 여기서
    이중 적용되지 않고, 사용자에게 반환되는 최상위 결과에만 한 번 정규화된다.
    """
    from backend.postprocess import normalize_ocr_text
    result = get_engine(engine_id).run(image, opts)
    for ln in result.lines:
        ln.text = normalize_ocr_text(ln.text)
    return result


def release_all() -> None:
    """캐시된 모든 엔진 인스턴스를 내리고 GPU(VRAM)를 반환한다.

    멀티엔진 비교에서 엔진을 '하나 올려 돌리고 → 내리고 → 다음 올리고'로
    순차 처리하기 위함. 여러 엔진 모델이 동시에 VRAM 을 점유하면 Ollama 가
    Qwen 을 올릴 자리가 부족하다 판단해 CPU 로 폴백(~200초/page)하는데,
    엔진 전환 사이에 VRAM 을 비워 Ollama 가 GPU 를 잡게 한다.
    """
    import gc
    for inst in list(_instances.values()):
        rel = getattr(inst, "release", None)
        if callable(rel):
            try:
                rel()
            except Exception:  # noqa: BLE001
                pass
    _instances.clear()
    gc.collect()
    try:
        import paddle
        if paddle.device.is_compiled_with_cuda():
            paddle.device.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass
