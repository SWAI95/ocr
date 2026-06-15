"""PaddleOCR-VL 어댑터 (VLM, 0.9B) — 벤치 4번째 엔진, hybrid 비교용.

PaddleOCR-VL-1.6-0.9B 를 `vl_rec_backend="native"` 로 **완전 in-process** 실행
(vLLM/llama.cpp/Docker 불필요 — 외부 서빙 백엔드 없이 동작). 이미지 → 레이아웃 +
블록별 VLM 인식 → 마크다운/표(HTML).

디바이스:
- 배포 RTX4090(sm_89): GPU 네이티브 동작(빠름).
- 개발 RTX5090(Blackwell sm_120): paddle cu126 의 flash-attn/cub 커널이 sm_120
  미지원이라 GPU 크래시. native VLM 은 CUDA 가 보이면 GPU 를 잡으므로, CPU 로
  돌리려면 **CUDA_VISIBLE_DEVICES= 로 서버를 띄워 GPU 를 숨겨야** 한다(느림).
  GPU 가 보이는데 사용 불가(Blackwell)면 명확한 안내 오류를 던진다.
"""
from __future__ import annotations

import os
import re
import time

import numpy as np

from backend.engines.base import (
    BBox, EngineOptions, LayoutRegion, OCREngine, OCRResult, TableResult, TextLine,
)
from backend.engines.paddle_engine import (
    _paddle_gpu_usable, _is_blackwell_gpu, _norm_label, _get)

_MD_STRIP = re.compile(r"[#*`>|]|<[^>]+>")


def _md_to_text(md: str) -> str:
    lines = []
    for ln in str(md).splitlines():
        t = _MD_STRIP.sub(" ", ln).strip()
        if t:
            lines.append(re.sub(r"\s{2,}", " ", t))
    return "\n".join(lines)


class PaddleVLEngine(OCREngine):
    name = "paddle_vl"
    capabilities = {"layout", "recognition", "table"}

    def __init__(self) -> None:
        self._pipe = None
        self._device = None

    def _resolve_device(self, use_gpu: bool) -> str:
        # 두 가지가 겹쳤다: ① markdown 추출 버그(res['markdown'] 직접 인덱싱으로 수정)
        # ② Blackwell(5090) GPU 에서 PaddleOCR-VL 의 VLM 생성이 빈 출력(sm_120 커널 한계).
        # 따라서 5090(Blackwell)은 CPU 로 폴백해야 정답이 나온다(느림, ~9분/page).
        # 4090(비-Blackwell)은 GPU 로 정상·빠름.
        if use_gpu and _paddle_gpu_usable() and not _is_blackwell_gpu():
            return "gpu:0"
        return "cpu"

    def _ensure(self, use_gpu: bool):
        device = self._resolve_device(use_gpu)
        if self._pipe is None or self._device != device:
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            from paddleocr import PaddleOCRVL
            self._pipe = PaddleOCRVL(
                vl_rec_backend="native", device=device,
                use_doc_orientation_classify=False, use_doc_unwarping=False)
            self._device = device
        return self._pipe, device

    def run(self, image: np.ndarray, opts: EngineOptions) -> OCRResult:
        pipe, device = self._ensure(opts.use_gpu)
        t0 = time.perf_counter()
        out = pipe.predict(image)
        dt = round((time.perf_counter() - t0) * 1000, 1)
        res = out[0]

        regions: list[LayoutRegion] = []
        lines: list[TextLine] = []
        tables: list[TableResult] = []

        # ① 레이아웃 영역 (검증된 포맷: boxes[*].coordinate/label/score)
        lay = _get(res, "layout_det_res", None)
        for b in (_get(lay, "boxes", []) or []) if lay is not None else []:
            coord = b.get("coordinate") if isinstance(b, dict) else None
            if coord is None:
                continue
            regions.append(LayoutRegion(
                bbox=BBox(*[float(v) for v in coord]),
                label=_norm_label(b.get("label", "")),
                score=float(b.get("score", 1.0)), raw_label=str(b.get("label", ""))))

        # ② 텍스트: markdown 이 VLM 의 표준 출력. 라인 단위로 분해(박스는 VLM
        #    특성상 부정확하므로 영역 박스 미부여 — 이게 VLM 의 알려진 약점).
        # PaddleOCRVLResult.markdown 은 keys() 에 없지만 __getitem__ 으로 접근된다.
        # (_get 은 `key in res` 체크라 None 을 반환 → 과거 0줄 버그의 원인.) 직접 인덱싱.
        try:
            md = res["markdown"]
        except Exception:
            md = _get(res, "markdown", None)
        md_txt = ""
        if isinstance(md, dict):
            md_txt = md.get("markdown_texts") or md.get("text") or ""
        elif md is not None:
            md_txt = str(md)
        for ln in _md_to_text(md_txt).splitlines():
            if ln.strip():
                lines.append(TextLine(polygon=[[0, 0]] * 4, text=ln.strip(), score=1.0))

        # ③ 표: markdown 의 <table>...</table> 추출
        for m in re.findall(r"<table[\s\S]*?</table>", md_txt, flags=re.IGNORECASE):
            tables.append(TableResult(bbox=BBox(0, 0, 0, 0), html=m))

        return OCRResult(
            engine=self.name, lines=lines, regions=regions, tables=tables,
            timings_ms={"vl_ms": dt}, device=device,
            meta={"model": "PaddleOCR-VL-1.6-0.9B", "backend": "native(in-process)"})
