"""EasyOCR 어댑터 (CRAFT 검출 + CRNN 인식). 레이아웃 없음.

모델은 config.EASYOCR_MODELS_DIR 에 저장/로드. 오프라인이면 download_enabled=False.
"""
from __future__ import annotations

import time

import numpy as np

from backend import config
from backend.engines.base import EngineOptions, OCREngine, OCRResult, TextLine

_LANG_MAP = {
    "korean": ["ko", "en"], "ko": ["ko", "en"],
    "en": ["en"], "english": ["en"],
    "ch": ["ch_sim", "en"],
}


class EasyOCREngine(OCREngine):
    name = "easyocr"
    capabilities = {"detection", "recognition"}

    def __init__(self) -> None:
        self._cache: dict[tuple, object] = {}

    def _reader(self, lang: str, use_gpu: bool):
        langs = tuple(_LANG_MAP.get(lang, ["ko", "en"]))
        key = (langs, use_gpu)
        if key not in self._cache:
            import easyocr
            self._cache[key] = easyocr.Reader(
                list(langs),
                gpu=use_gpu and config.resolve_device(True) == "gpu",
                model_storage_directory=str(config.EASYOCR_MODELS_DIR),
                download_enabled=not config.OFFLINE,
            )
        return self._cache[key]

    def run(self, image: np.ndarray, opts: EngineOptions) -> OCRResult:
        reader = self._reader(opts.lang, opts.use_gpu)
        t0 = time.perf_counter()
        raw = reader.readtext(image)  # [(bbox, text, conf), ...]
        dt = round((time.perf_counter() - t0) * 1000, 1)

        lines: list[TextLine] = []
        for box, text, conf in raw:
            poly = [[float(p[0]), float(p[1])] for p in box]
            lines.append(TextLine(polygon=poly, text=str(text), score=float(conf)))

        device = "gpu" if (opts.use_gpu and config.resolve_device(True) == "gpu") else "cpu"
        return OCRResult(engine=self.name, lines=lines, regions=[],
                         timings_ms={"det_rec_ms": dt}, device=device,
                         meta={"langs": _LANG_MAP.get(opts.lang, ["ko", "en"])})
