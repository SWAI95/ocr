"""PaddleOCR-VL 어댑터 (VLM, 0.9B) — 벤치 4번째 엔진, hybrid 비교용.

PaddleOCR-VL-1.6-0.9B 를 `vl_rec_backend="native"` 로 **완전 in-process** 실행
(vLLM/llama.cpp/Docker 불필요 — 외부 서빙 백엔드 없이 동작). 이미지 → 레이아웃 +
블록별 VLM 인식 → 마크다운/표(HTML).

디바이스 (2026-06 갱신 — cu129 휠 + markdown 접근자 수정 후 실측):
- 배포 RTX4090(sm_89): GPU 네이티브 동작(빠름).
- 개발 RTX5090(Blackwell sm_120): **cu129 휠에선 GPU 정상 동작**. 실측 contract_ko
  4.8s / doc_02 폼 11.5s, 크래시 없음. 과거 '5090 GPU 빈 출력/크래시'로 알려졌던 건
  실은 ① cu126 의 sm_120 미지원 ② markdown 을 `res["markdown"]` 로 잘못 인덱싱해
  전 디바이스에서 빈 출력(진짜 원인)이 겹친 것. 지금은 `res.markdown`(@property)로
  수정 + cu129 라 Blackwell GPU 도 정상. OCR_PADDLE_VL_GPU=off 로 CPU 강제 가능.
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
    _paddle_gpu_usable, _norm_label, _get)

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

    def release(self) -> None:
        """VL 파이프라인을 내려 GPU(VRAM)를 반환(비교 시 엔진 전환 간)."""
        self._pipe = None
        self._device = None

    def _resolve_device(self, use_gpu: bool) -> str:
        # cu129 + markdown 접근자 수정 후 Blackwell(5090) GPU 도 정상 동작 확인(실측).
        # paddle_engine._device_str 과 동일 규칙 — GPU 커널 프로브 통과면 GPU.
        # OCR_PADDLE_VL_GPU=off 로 CPU 강제(디버그/비교용).
        if os.environ.get("OCR_PADDLE_VL_GPU", "auto").lower() in ("0", "off", "false"):
            return "cpu"
        return "gpu:0" if (use_gpu and _paddle_gpu_usable()) else "cpu"

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
        # paddleocr 3.6.0: markdown 은 @property(res.markdown)로 dict 를 반환하고
        # 텍스트 키는 markdown_texts. 과거 버전의 res["markdown"] 인덱싱은 예외를
        # 던져 '전부 빈 출력' 버그를 냈다(→ 5090 GPU 가 깨진 걸로 오인됐던 진짜 원인).
        # property 우선, 실패 시 구버전 인덱싱으로 폴백.
        md = None
        for _accessor in (lambda: res.markdown, lambda: res["markdown"]):
            try:
                md = _accessor()
                if md:
                    break
            except Exception:
                continue
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
