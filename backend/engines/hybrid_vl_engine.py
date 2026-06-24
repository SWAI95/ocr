"""하이브리드 엔진 — 페이지를 레이아웃으로 분류해 최적 인식기로 라우팅.

Paddle 의 한 줄(single text-line) 인식기는 양쪽정렬 줄글에서 줄바꿈마다 박스를
끊어 둘째 줄부터 무너진다(doc_00 p2 WER 54.6%). 반면 폼/표는 Paddle 의
PPStructureV3 가 구조·CER 에서 압도적(p1 폼 CER 0.71% vs Qwen 16.8%).

그래서 **Paddle 레이아웃으로 페이지를 먼저 분류**하고:
  - table 영역이 페이지를 지배(>=임계) 하거나 도장 인식이면 → Paddle(PPStructureV3)
  - 그 외(줄글 지배) → Qwen2.5VL(VLM, 페이지를 통째로 읽어 줄바꿈 단락 복원)
로 보낸다. 페이지가 동질적이라 페이지 단위 라우팅(1콜/페이지)으로 충분하다.

근거: memory `paddle-vs-vlm-rec` (doc_00 실측, 폼 Paddle 0.71% / 줄글·표 Qwen WER 1/3).
"""
from __future__ import annotations

import difflib
import os
import re
import time
from dataclasses import replace

import numpy as np

from backend.engines.base import EngineOptions, LABEL_TABLE, OCREngine, OCRResult, TextLine
from backend.models.registry import default_model

# 안전장치 임계 — Paddle(결정론) 텍스트엔 있는데 Qwen 엔 통째로 빠진 '최장 누락
# 구간' 이 이 값 이상이면, greedy VLM 이 밀집 구간에서 한 덩어리를 전치·누락한
# 사고로 보고 그 페이지만 Paddle 결과로 교체한다.
# (doc_00 실측: 정상 줄글 hole≈22~23, 사고 페이지 hole≈46~50 → 35 가 안전한 분리선)
_HOLE_THRESHOLD = 35


def _longest_missing_run(ref: str, hyp: str, win: int = 12) -> int:
    """ref(Paddle) 의 길이 win 윈도우가 hyp(Qwen) 에 연속으로 없는 개수
    (= Qwen 이 통째로 누락한 최장 구간 근사). 공백 무시."""
    r = re.sub(r"\s+", "", ref)
    h = re.sub(r"\s+", "", hyp)
    if len(r) < win:
        return 0
    best = cur = 0
    for i in range(len(r) - win + 1):
        if r[i:i + win] not in h:
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return best


def _merge_paddle_chars_qwen_spacing(qwen_text: str, paddle_text: str) -> str:
    """Paddle 글자(정확도 우선) 위에 Qwen 의 공백·줄바꿈(문서 충실)을 입혀 병합.

    안전장치가 발동한 페이지(=Qwen 이 한 덩어리를 누락·전치한 사고)에서, Paddle 의
    옳은 글자를 취하되 Qwen 의 띄어쓰기·줄바꿈을 보존한다. Paddle 단독 교체는 CER 은
    좋아도 한국어 띄어쓰기가 뭉개져 WER 이 폭증하는데, 이 병합은 CER·WER 을 둘 다
    살린다(doc_00 실측: Paddle교체 WER 24.1% → 병합 13.5%, CER 1.79%→1.55%).
    문서 실제 띄어쓰기는 이미지를 읽은 Qwen 만 알기에 알고리즘 spacer 로는 대체 불가.
    """
    qss = "".join(c for c in qwen_text if not c.isspace())
    ps = "".join(c for c in paddle_text if not c.isspace())
    # ws[k]: Qwen 의 k번째 비공백 문자 뒤 공백("" | " " | "\n") — 줄바꿈 우선
    ws: dict[int, str] = {}
    k = -1
    for i, c in enumerate(qwen_text):
        if not c.isspace():
            k += 1
            j = i + 1
            nl = sp = False
            while j < len(qwen_text) and qwen_text[j].isspace():
                if qwen_text[j] == "\n":
                    nl = True
                else:
                    sp = True
                j += 1
            ws[k] = "\n" if nl else (" " if sp else "")
    out: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, qss, ps, autojunk=False).get_opcodes():
        if tag == "equal":
            for off in range(i2 - i1):
                out.append(ps[j1 + off])
                out.append(ws.get(i1 + off, ""))
        elif tag == "replace":          # 불일치 → Paddle 글자 채택(CER)
            out.append(ps[j1:j2])
            out.append(ws.get(i2 - 1, ""))
        elif tag == "delete":           # Paddle 누락 → Qwen 글자 유지
            for off in range(i2 - i1):
                out.append(qss[i1 + off])
                out.append(ws.get(i1 + off, ""))
        elif tag == "insert":           # Qwen 누락 → Paddle 글자 복구
            out.append(ps[j1:j2])
            if i1 > 0:
                out.append(ws.get(i1 - 1, ""))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in "".join(out).split("\n")]
    return "\n".join(ln for ln in lines if ln)


class HybridVLEngine(OCREngine):
    name = "hybrid_vl"
    capabilities = {"layout", "detection", "recognition", "table", "seal"}

    # table 영역 면적이 페이지의 이 비율 이상이면 폼/표로 보고 Paddle 로 라우팅.
    # (doc_00: 폼 페이지 table 비율 ~0.58, 줄글 페이지 ~0.0 → 0.25 면 안전한 분리)
    TABLE_AREA_THRESHOLD = 0.25

    def _paddle(self) -> OCREngine:
        from backend.pipeline import runner
        return runner.get_engine("paddle")

    def _ollama(self) -> OCREngine:
        from backend.pipeline import runner
        return runner.get_engine("ollama_vl")

    def _vlm(self) -> tuple[OCREngine, str]:
        """줄글 경로의 VLM 백엔드 선택 → (엔진, 라벨).

        OCR_HYBRID_VLM: auto(기본) | ollama_vl(Qwen) | paddle_vl(in-process, offline).
        auto = Ollama 가 떠 있으면 Qwen, 아니면 paddle_vl 로 폴백 → Ollama 가 없는
        오프라인 배포에서도 hybrid 가 에러 없이 reading-order 를 복원한다(5090/4090 GPU).
        실측(데이터셋): paddle_vl 백엔드로 hybrid 평균 CER 2.3%(paddle 3.1%, 완전 offline).
        """
        from backend.pipeline import runner
        choice = os.environ.get("OCR_HYBRID_VLM", "auto").lower()
        if choice == "paddle_vl":
            return runner.get_engine("paddle_vl"), "paddle_vl"
        if choice == "auto":
            try:
                from backend.engines.ollama_vl_engine import _list_models, _DEFAULT_HOST
                if _list_models(_DEFAULT_HOST):
                    return runner.get_engine("ollama_vl"), "ollama_vl"
            except Exception:  # noqa: BLE001
                pass
            if runner.is_available("paddle_vl"):
                return runner.get_engine("paddle_vl"), "paddle_vl"
        return runner.get_engine("ollama_vl"), "ollama_vl"

    @staticmethod
    def _table_ratio(regions, image: np.ndarray) -> float:
        h, w = image.shape[:2]
        page = float(w * h) or 1.0
        area = sum(r.bbox.width * r.bbox.height
                   for r in regions if r.label == LABEL_TABLE)
        return area / page

    def run(self, image: np.ndarray, opts: EngineOptions) -> OCRResult:
        paddle = self._paddle()
        layout_model = opts.layout_model or default_model("paddle", "layout")

        # 1) 레이아웃만 빠르게 실행 — 페이지 분류용(OCR 전)
        t0 = time.perf_counter()
        regions = paddle._run_layout(image, layout_model, opts.use_gpu)
        layout_ms = round((time.perf_counter() - t0) * 1000, 1)
        ratio = self._table_ratio(regions, image)
        route_paddle = ratio >= self.TABLE_AREA_THRESHOLD or opts.use_seal

        # 2a) 표/폼/도장 지배 → Paddle PPStructureV3 (구조 + CER 강점)
        if route_paddle:
            popts = replace(opts, use_table=True, rec_model=None, det_model=None,
                            layout_model=layout_model)
            res = paddle.run(image, popts)
            res.engine = self.name
            res.timings_ms = {"layout_ms": layout_ms, **res.timings_ms}
            res.meta = {**res.meta, "route": "paddle", "table_ratio": round(ratio, 3)}
            return res

        # 2b) 줄글 지배 → VLM (줄바꿈 단락 통째 인식, reading-order 복원). 백엔드는
        #     Qwen(ollama) 또는 paddle_vl(in-process, offline) 중 선택(_vlm). VLM 은
        #     좌표가 없어 오버레이용으로 Paddle 영역 박스를 붙여 준다.
        vlm, vlm_label = self._vlm()
        res = vlm.run(image, opts)
        res.engine = self.name
        res.regions = regions
        res.timings_ms = {"layout_ms": layout_ms, **res.timings_ms}
        res.meta = {**res.meta, "route": vlm_label, "table_ratio": round(ratio, 3)}

        # 안전장치(OCR_HYBRID_SAFETY=off 로 비활성): greedy VLM 이 밀집 구간에서
        # 가끔 한 덩어리를 전치·누락하는 비결정 사고를 '그 페이지만' 구제한다.
        # Paddle 텍스트엔 있는데 Qwen 엔 통째로 빠진 구간(hole)이 임계 이상이면,
        # Paddle 의 옳은 글자에 Qwen 의 띄어쓰기·줄바꿈을 입혀 '병합'한다(_merge…).
        # → CER 은 Paddle 수준으로 내리고 WER(가독성)은 Qwen 수준 유지. 정상 줄글은
        #   Qwen 그대로. 비용: 줄글 페이지마다 Paddle 참조 1회(5090 dev CPU, 배포 4090 GPU).
        if os.environ.get("OCR_HYBRID_SAFETY", "on").lower() not in ("0", "off", "false"):
            popts = replace(opts, use_table=True, rec_model=None, det_model=None,
                            layout_model=layout_model)
            pres = paddle.run(image, popts)
            hole = _longest_missing_run(pres.full_text, res.full_text)
            # 항상 병합: Paddle 글자(정확) + Qwen 띄어쓰기(가독성). 조건부(hole>=35)는
            # Qwen 롤에 따라 발동/미발동이 갈려 CER 이 1.5~2.2 로 출렁였는데, 항상 병합은
            # 결정론적 Paddle 글자에 기대 ~1.6 으로 안정적이다(WER 도 Qwen 띄어쓰기로 유지).
            # hole 은 진단용 메타로만 기록(_HOLE_THRESHOLD 미사용).
            merged = _merge_paddle_chars_qwen_spacing(res.full_text, pres.full_text)
            res.lines = [TextLine(polygon=[[0, 0]] * 4, text=ln, score=1.0)
                         for ln in merged.splitlines() if ln.strip()]
            res.timings_ms = {"layout_ms": layout_ms, **res.timings_ms,
                              **pres.timings_ms}
            res.meta = {**res.meta, "route": f"{vlm_label}+paddle(병합)",
                        "table_ratio": round(ratio, 3), "hole": hole,
                        "merge": "always"}
            return res
        return res
