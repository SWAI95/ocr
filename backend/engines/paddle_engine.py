"""PaddleOCR 3.x 어댑터 (Primary 엔진).

- det+rec: 고수준 `PaddleOCR`(회전 박스 crop/보정이 견고) — det/rec 모델명을
  각각 지정 가능 → "Detection/Recognition 모델 각각 선택" 요구 충족.
- layout: `LayoutDetection`(PP-DocLayout) 별도 호출 → 객체 인식(영역 구분).
- table/seal: Phase 3 에서 PPStructureV3 로 확장.

모델은 인스턴스 캐시로 1회만 로드. 오프라인이면 로컬 model_dir 사용
(config.OFFLINE / PADDLE_MODELS_DIR).
"""
from __future__ import annotations

import os
import time
from typing import Optional

import numpy as np

from backend import config
from backend.engines.base import (
    BBox, EngineOptions, LayoutRegion, OCREngine, OCRResult, TableResult, TextLine,
    LABEL_TEXT, LABEL_TITLE, LABEL_TABLE, LABEL_FIGURE, LABEL_FOOTNOTE,
    LABEL_HEADER, LABEL_FOOTER, LABEL_SEAL, LABEL_FORMULA, LABEL_OTHER,
)
from backend.models.registry import default_model

# PP-DocLayout 라벨 → 표준 라벨 정규화
_LAYOUT_LABEL_MAP = {
    "text": LABEL_TEXT, "paragraph": LABEL_TEXT, "content": LABEL_TEXT,
    "abstract": LABEL_TEXT, "reference": LABEL_TEXT,
    "title": LABEL_TITLE, "doc_title": LABEL_TITLE,
    "paragraph_title": LABEL_TITLE, "table_title": LABEL_TITLE,
    "figure_title": LABEL_TITLE, "chart_title": LABEL_TITLE,
    "table": LABEL_TABLE,
    "figure": LABEL_FIGURE, "image": LABEL_FIGURE, "chart": LABEL_FIGURE,
    "footnote": LABEL_FOOTNOTE,
    "header": LABEL_HEADER, "header_image": LABEL_HEADER,
    "footer": LABEL_FOOTER, "footer_image": LABEL_FOOTER, "number": LABEL_FOOTER,
    "seal": LABEL_SEAL, "stamp": LABEL_SEAL,
    "formula": LABEL_FORMULA, "equation": LABEL_FORMULA,
}


def _norm_label(raw: str) -> str:
    return _LAYOUT_LABEL_MAP.get(str(raw).strip().lower(), LABEL_OTHER)


_paddle_gpu_ok: Optional[bool] = None


def _paddle_gpu_usable() -> bool:
    """PaddlePaddle GPU 사용 가능 여부 (런타임 conv 프로브).

    paddle GPU 커널이 현재 GPU 에서 실제로 동작하는지 작은 Conv2D 로 직접 검증한다.
    예전 cu126 휠은 Blackwell(sm_120)에서 conv 가 '조용히 0'을 반환했는데, 이를
    하드코딩(아키텍처 차단)하는 대신 결과가 0 인지 직접 본다 → cu129 등 sm_120 지원
    휠을 깔면 5090 에서도 자동으로 GPU 사용(4090 sm_89 등도 동일). cu126 을 쓰면
    프로브가 0 이라 자동 CPU 폴백. 환경변수 OCR_PADDLE_GPU=force|off 로 강제/차단.
    """
    global _paddle_gpu_ok
    if _paddle_gpu_ok is not None:
        return _paddle_gpu_ok
    override = os.environ.get("OCR_PADDLE_GPU", "auto").lower()
    try:
        import paddle
        if not paddle.device.is_compiled_with_cuda() or \
                paddle.device.cuda.device_count() == 0:
            _paddle_gpu_ok = False
        elif override == "force":
            _paddle_gpu_ok = True
        elif override == "off":
            _paddle_gpu_ok = False
        else:
            # 실제 GPU conv 결과가 0 이 아닌지 프로브(미지원 휠/아키텍처면 조용히 0)
            import paddle.nn as _nn
            paddle.set_device("gpu:0")
            probe = float(_nn.Conv2D(3, 4, 3)(paddle.ones([1, 3, 8, 8])).abs().sum())
            _paddle_gpu_ok = probe > 0.0
    except Exception:
        _paddle_gpu_ok = False
    return _paddle_gpu_ok


def _device_str(use_gpu: bool) -> str:
    return "gpu:0" if (use_gpu and _paddle_gpu_usable()) else "cpu"


def _poly_to_list(poly) -> list[list[float]]:
    arr = np.asarray(poly, dtype=float).reshape(-1, 2)
    return [[float(x), float(y)] for x, y in arr]


def _get(res, key, default=None):
    """PaddleOCR 3.x 결과(dict 유사/객체) 방어적 접근."""
    try:
        if key in res:
            return res[key]
    except Exception:
        pass
    try:
        j = res.json
        inner = j.get("res", j) if isinstance(j, dict) else {}
        return inner.get(key, default)
    except Exception:
        return default


def _reading_order_sort(lines: list[TextLine]) -> list[TextLine]:
    """라인을 읽기순서(위→아래, 같은 행은 왼→오)로 정렬 — region-aware.

    양쪽정렬 줄글에서 한 줄이 여러 박스로 쪼개지거나 검출 순서가 뒤섞이는 문제를
    바로잡는다. 단, **표(region_label=='table') 라인은 2D 구조라 단순 정렬이 셀을
    뒤섞으므로 원래 순서를 보존**한다(같은 밴드 내 원 인덱스 유지). 줄글만 기하 정렬.
    """
    if len(lines) < 2:
        return lines

    # 표 우세 페이지(폼/표)는 네이티브 순서가 이미 정답 → 정렬하면 오히려 해로움.
    # 줄글 페이지만 정렬해 양쪽정렬 조각/뒤섞임을 교정한다.
    n_table = sum(1 for l in lines if l.region_label == LABEL_TABLE)
    if n_table > 0.5 * len(lines):
        return lines

    def cy(l):
        ys = [p[1] for p in l.polygon]
        return sum(ys) / len(ys)

    def cx(l):
        xs = [p[0] for p in l.polygon]
        return sum(xs) / len(xs)

    heights = sorted(max(p[1] for p in l.polygon) - min(p[1] for p in l.polygon)
                     for l in lines)
    band = max(8.0, (heights[len(heights) // 2] or 20) * 0.6)

    def geo(ls):  # 줄글 기하 정렬(밴드별 위→아래, 줄 안 왼→오)
        return sorted(ls, key=lambda l: (round(cy(l) / band), cx(l)))

    tables = [l for l in lines if l.region_label == LABEL_TABLE]
    if not tables:
        return geo(lines)
    # 표 라인은 통째로 원순서 블록 유지, 비표 줄글만 정렬해 표 위/아래에 배치
    tb_top = min(cy(l) for l in tables)
    others = geo([l for l in lines if l.region_label != LABEL_TABLE])
    above = [l for l in others if cy(l) < tb_top]
    below = [l for l in others if cy(l) >= tb_top]
    return above + tables + below


class PaddleEngine(OCREngine):
    name = "paddle"
    capabilities = {"layout", "detection", "recognition", "table", "seal"}

    def __init__(self) -> None:
        self._ocr_cache: dict[tuple, object] = {}
        self._layout_cache: dict[str, object] = {}
        self._struct_cache: dict[tuple, object] = {}

    def release(self) -> None:
        """로드된 모델 캐시를 비워 GPU(VRAM)를 반환(비교 시 엔진 전환 간)."""
        self._ocr_cache.clear()
        self._layout_cache.clear()
        self._struct_cache.clear()

    # --- 모델 로더 (캐시) ---
    def _get_ocr(self, det_model: str, rec_model: str, lang: str,
                 use_gpu: bool, use_ori: bool, det_params: Optional[dict] = None):
        det_params = det_params or {}
        key = (det_model, rec_model, lang, use_gpu, use_ori,
               tuple(sorted(det_params.items())))
        if key not in self._ocr_cache:
            from paddleocr import PaddleOCR
            kwargs = dict(
                lang=lang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=use_ori,
                device=_device_str(use_gpu),
                enable_mkldnn=False,  # paddle 3.3.x CPU oneDNN 버그 회피
            )
            if det_model:
                kwargs["text_detection_model_name"] = det_model
            if rec_model:
                kwargs["text_recognition_model_name"] = rec_model
            kwargs.update(det_params)  # text_det_limit_side_len 등 인식률 튜닝
            # 오프라인 로딩은 config.apply_offline_env() 가 설정한
            # PADDLE_PDX_CACHE_HOME(번들 캐시) 로 일괄 처리된다.
            self._ocr_cache[key] = PaddleOCR(**kwargs)
        return self._ocr_cache[key]

    def _get_structure(self, rec_model: str, layout_model: str, use_gpu: bool,
                       det_params: Optional[dict] = None):
        """PPStructureV3 (레이아웃+OCR+표+도장). 수식/차트는 끔(크기·속도).

        한국어는 반드시 korean rec 모델 + textline_orientation=False 여야 정상
        (기본 다국어 모델은 한글을 한자로 오인식, orientation 켜면 셀 180° 뒤집힘).
        """
        det_params = det_params or {}
        key = (rec_model, layout_model, use_gpu, tuple(sorted(det_params.items())))
        if key not in self._struct_cache:
            from paddleocr import PPStructureV3
            kwargs = dict(
                device=_device_str(use_gpu),
                enable_mkldnn=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                use_formula_recognition=False,
                use_chart_recognition=False,
            )
            if rec_model:
                kwargs["text_recognition_model_name"] = rec_model
            if layout_model:
                kwargs["layout_detection_model_name"] = layout_model
            kwargs.update(det_params)  # 인식률 튜닝(limit_side_len, box_thresh 등)
            self._struct_cache[key] = PPStructureV3(**kwargs)
        return self._struct_cache[key]

    def _get_layout(self, layout_model: str, use_gpu: bool):
        key = f"{layout_model}|{use_gpu}"
        if key not in self._layout_cache:
            from paddleocr import LayoutDetection
            kwargs = dict(model_name=layout_model, device=_device_str(use_gpu),
                          enable_mkldnn=False)
            self._layout_cache[key] = LayoutDetection(**kwargs)
        return self._layout_cache[key]

    # --- 단계별 ---
    def _run_layout(self, image: np.ndarray, layout_model: str,
                    use_gpu: bool) -> list[LayoutRegion]:
        model = self._get_layout(layout_model, use_gpu)
        out = model.predict(image, batch_size=1)
        regions: list[LayoutRegion] = []
        for res in out:
            boxes = _get(res, "boxes", []) or []
            for b in boxes:
                coord = b.get("coordinate") if isinstance(b, dict) else None
                label = b.get("label", "") if isinstance(b, dict) else ""
                score = b.get("score", 1.0) if isinstance(b, dict) else 1.0
                if coord is None:
                    continue
                x1, y1, x2, y2 = [float(v) for v in coord]
                regions.append(LayoutRegion(
                    bbox=BBox(x1, y1, x2, y2), label=_norm_label(label),
                    score=float(score), raw_label=str(label)))
        return regions

    def _run_ocr(self, image: np.ndarray, det_model: str, rec_model: str,
                 lang: str, use_gpu: bool, use_ori: bool,
                 det_params: Optional[dict] = None) -> list[TextLine]:
        ocr = self._get_ocr(det_model, rec_model, lang, use_gpu, use_ori, det_params)
        out = ocr.predict(image)
        lines: list[TextLine] = []
        for res in out:
            texts = _get(res, "rec_texts", []) or []
            scores = _get(res, "rec_scores", []) or []
            polys = _get(res, "rec_polys", None)
            if polys is None:
                polys = _get(res, "dt_polys", []) or []
            for i, txt in enumerate(texts):
                poly = polys[i] if i < len(polys) else [[0, 0], [0, 0], [0, 0], [0, 0]]
                sc = float(scores[i]) if i < len(scores) else 1.0
                lines.append(TextLine(polygon=_poly_to_list(poly),
                                      text=str(txt), score=sc))
        return _reading_order_sort(lines)

    @staticmethod
    def _assign_regions(lines: list[TextLine],
                        regions: list[LayoutRegion]) -> None:
        """텍스트 라인 중심점이 속한 레이아웃 영역 라벨을 부여."""
        if not regions:
            return
        for ln in lines:
            xs = [p[0] for p in ln.polygon]
            ys = [p[1] for p in ln.polygon]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            best = None
            for rg in regions:
                b = rg.bbox
                if b.x1 <= cx <= b.x2 and b.y1 <= cy <= b.y2:
                    area = b.width * b.height
                    if best is None or area < best[1]:
                        best = (rg.label, area)
            if best:
                ln.region_label = best[0]

    @staticmethod
    def _bbox_from_cells(cells) -> BBox:
        xs: list[float] = []
        ys: list[float] = []
        for c in cells:
            arr = np.asarray(c, dtype=float).reshape(-1, 2)
            xs += [float(arr[:, 0].min()), float(arr[:, 0].max())]
            ys += [float(arr[:, 1].min()), float(arr[:, 1].max())]
        if not xs:
            return BBox(0, 0, 0, 0)
        return BBox(min(xs), min(ys), max(xs), max(ys))

    def _run_structure(self, image: np.ndarray, rec_model: str,
                       layout_model: str, use_gpu: bool,
                       det_params: Optional[dict] = None):
        """PPStructureV3 경로 — 레이아웃+OCR+표+도장을 한 번에.

        표 셀 텍스트는 메인 한국어 OCR 을 매칭해서 채운다
        (use_ocr_results_with_table_cells=True). e2e 표 인식 모델은 자체
        디코더가 한글을 깨뜨리므로 끈다(use_e2e_*_table_rec_model=False).
        표 방향 분류기는 작은 실사 셀을 180° 뒤집어 한글을 깨뜨리므로 끈다
        (use_table_orientation_classify=False) — 이게 빈 폼 표 깨짐의 주원인이었다.
        """
        pipe = self._get_structure(rec_model, layout_model, use_gpu, det_params)
        out = pipe.predict(
            image,
            use_ocr_results_with_table_cells=True,
            use_e2e_wired_table_rec_model=False,
            use_e2e_wireless_table_rec_model=False,
            use_table_orientation_classify=False,
        )
        regions: list[LayoutRegion] = []
        lines: list[TextLine] = []
        tables: list[TableResult] = []
        for res in out:
            lay = _get(res, "layout_det_res", None)
            for b in (_get(lay, "boxes", []) or []) if lay is not None else []:
                coord = b.get("coordinate") if isinstance(b, dict) else None
                if coord is None:
                    continue
                regions.append(LayoutRegion(
                    bbox=BBox(*[float(v) for v in coord]),
                    label=_norm_label(b.get("label", "")),
                    score=float(b.get("score", 1.0)),
                    raw_label=str(b.get("label", ""))))
            ocr = _get(res, "overall_ocr_res", None)
            if ocr is not None:
                texts = _get(ocr, "rec_texts", []) or []
                scores = _get(ocr, "rec_scores", []) or []
                polys = _get(ocr, "rec_polys", []) or []
                for i, txt in enumerate(texts):
                    poly = polys[i] if i < len(polys) else [[0, 0]] * 4
                    sc = float(scores[i]) if i < len(scores) else 1.0
                    lines.append(TextLine(_poly_to_list(poly), str(txt), sc))
            for t in _get(res, "table_res_list", []) or []:
                cells = t.get("cell_box_list", []) if hasattr(t, "get") else []
                tables.append(TableResult(
                    bbox=self._bbox_from_cells(cells),
                    html=str(t.get("pred_html", "")) if hasattr(t, "get") else ""))
            for s in _get(res, "seal_res_list", []) or []:
                stexts = _get(s, "rec_texts", []) or []
                spolys = _get(s, "rec_polys", []) or []
                sscores = _get(s, "rec_scores", []) or []
                for i, txt in enumerate(stexts):
                    poly = spolys[i] if i < len(spolys) else [[0, 0]] * 4
                    sc = float(sscores[i]) if i < len(sscores) else 1.0
                    lines.append(TextLine(_poly_to_list(poly), str(txt), sc,
                                          region_label=LABEL_SEAL))
        # 먼저 영역 라벨 부여 → region-aware 읽기순서 정렬(표는 원순서 보존)
        self._assign_regions([l for l in lines if l.region_label is None], regions)
        lines = _reading_order_sort(lines)
        return regions, lines, tables

    # 인식률 튜닝용 det 파라미터(EngineOptions.extra 로 전달)
    _DET_PARAM_KEYS = ("text_det_limit_side_len", "text_det_limit_type",
                       "text_det_thresh", "text_det_box_thresh",
                       "text_det_unclip_ratio")

    # --- 엔드투엔드 ---
    def run(self, image: np.ndarray, opts: EngineOptions) -> OCRResult:
        det = opts.det_model or default_model("paddle", "detection")
        rec = opts.rec_model or default_model("paddle", "recognition")
        layout_model = opts.layout_model or default_model("paddle", "layout")
        det_params = {k: opts.extra[k] for k in self._DET_PARAM_KEYS
                      if k in opts.extra}
        # 측정으로 확인된 인식률 향상: 검출 입력을 1536으로 캡(모델 학습 해상도에
        # 맞춰 다운스케일)하면 문서 CER 5.7%→4.62%. 명시 지정 없으면 기본 적용.
        if "text_det_limit_side_len" not in det_params:
            det_params["text_det_limit_side_len"] = 1536
            det_params["text_det_limit_type"] = "max"
        timings: dict[str, float] = {}
        device = _device_str(opts.use_gpu)

        # 표/도장이 필요하면 PPStructureV3 통합 경로
        if opts.use_table or opts.use_seal:
            t0 = time.perf_counter()
            regions, lines, tables = self._run_structure(
                image, rec, layout_model, opts.use_gpu, det_params)
            timings["structure_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return OCRResult(
                engine=self.name, lines=lines, regions=regions, tables=tables,
                timings_ms=timings, device=device,
                meta={"pipeline": "PPStructureV3", "rec_model": rec,
                      "layout_model": layout_model,
                      "table": opts.use_table, "seal": opts.use_seal})

        # 일반 경로: (옵션)레이아웃 + det/rec
        regions = []
        if opts.use_layout:
            t0 = time.perf_counter()
            regions = self._run_layout(image, layout_model, opts.use_gpu)
            timings["layout_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        t0 = time.perf_counter()
        lines = self._run_ocr(image, det, rec, opts.lang, opts.use_gpu,
                              use_ori=opts.extra.get("use_textline_orientation", True),
                              det_params=det_params)
        timings["det_rec_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        self._assign_regions(lines, regions)

        return OCRResult(
            engine=self.name, lines=lines, regions=regions,
            timings_ms=timings, device=device,
            meta={"det_model": det, "rec_model": rec,
                  "layout_model": layout_model if opts.use_layout else None},
        )
