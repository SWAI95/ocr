"""Tesseract 5 어댑터 (LSTM). baseline 비교용.

전제: tesseract 바이너리 + kor/eng traineddata 설치(또는 models/tessdata).
오프라인이면 TESSDATA_PREFIX 를 models/tessdata 로 지정해 로컬 traineddata 사용.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict

import numpy as np

from backend import config
from backend.engines.base import EngineOptions, OCREngine, OCRResult, TextLine

_LANG_MAP = {"korean": "kor+eng", "ko": "kor", "en": "eng", "english": "eng"}


class TesseractEngine(OCREngine):
    name = "tesseract"
    capabilities = {"detection", "recognition"}

    def __init__(self) -> None:
        # 로컬 traineddata 가 있으면 우선 사용
        tdir = config.TESSDATA_DIR
        if any(tdir.glob("*.traineddata")):
            os.environ.setdefault("TESSDATA_PREFIX", str(tdir))

    def _lang(self, opts: EngineOptions) -> str:
        if opts.rec_model:
            return opts.rec_model
        return _LANG_MAP.get(opts.lang, "kor+eng")

    def run(self, image: np.ndarray, opts: EngineOptions) -> OCRResult:
        import pytesseract
        from pytesseract import Output

        lang = self._lang(opts)
        t0 = time.perf_counter()
        data = pytesseract.image_to_data(image, lang=lang,
                                         output_type=Output.DICT)
        dt = round((time.perf_counter() - t0) * 1000, 1)

        # 단어 → (block,par,line) 단위로 그룹핑
        groups: dict[tuple, list[int]] = defaultdict(list)
        n = len(data["text"])
        for i in range(n):
            txt = data["text"][i].strip()
            if not txt:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            groups[key].append(i)

        lines: list[TextLine] = []
        for key, idxs in groups.items():
            words = [data["text"][i] for i in idxs]
            confs = [float(data["conf"][i]) for i in idxs if float(data["conf"][i]) >= 0]
            xs1 = [data["left"][i] for i in idxs]
            ys1 = [data["top"][i] for i in idxs]
            xs2 = [data["left"][i] + data["width"][i] for i in idxs]
            ys2 = [data["top"][i] + data["height"][i] for i in idxs]
            x1, y1, x2, y2 = min(xs1), min(ys1), max(xs2), max(ys2)
            poly = [[float(x1), float(y1)], [float(x2), float(y1)],
                    [float(x2), float(y2)], [float(x1), float(y2)]]
            score = (sum(confs) / len(confs) / 100.0) if confs else 0.0
            lines.append(TextLine(polygon=poly, text=" ".join(words), score=score))

        return OCRResult(engine=self.name, lines=lines, regions=[],
                         timings_ms={"ocr_ms": dt}, device="cpu",
                         meta={"lang": lang})
