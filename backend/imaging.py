"""이미지 입출력 / 시각화 — 업로드 디코드, PDF 변환, 오버레이 렌더링.

내부 이미지 표준: OpenCV 관례인 BGR np.ndarray (H,W,3).
"""
from __future__ import annotations

import base64
import io
import os

import cv2
import numpy as np

from backend.engines.base import LayoutRegion, TextLine

# PDF 최대 페이지 수 — 환경변수 OCR_MAX_PAGES 로 조정(기본 100). 거대한 PDF 가
# 메모리/시간을 폭주시키는 것만 막는 안전 상한이며, 필요하면 더 올려도 된다.
_MAX_PDF_PAGES = int(os.environ.get("OCR_MAX_PAGES", "100"))

# 레이아웃 라벨별 색(BGR)
_REGION_COLORS = {
    "text": (0, 170, 0), "title": (0, 0, 220), "table": (200, 0, 0),
    "figure": (0, 140, 255), "footnote": (180, 0, 180), "header": (120, 120, 120),
    "footer": (120, 120, 120), "seal": (0, 0, 255), "formula": (200, 120, 0),
    "other": (90, 90, 90),
}


def decode_image(data: bytes, filename: str = "", dpi: int = 200) -> list[np.ndarray]:
    """업로드 바이트 → BGR 이미지 리스트(PDF는 페이지별). dpi는 PDF 렌더 해상도."""
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        return _pdf_to_images(data, dpi=dpi)
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("이미지 디코드 실패 (지원 형식: png/jpg/bmp/tiff/pdf)")
    return [img]


def _pdf_to_images(data: bytes, dpi: int = 200, max_pages: int = _MAX_PDF_PAGES) -> list[np.ndarray]:
    """PDF → BGR 이미지 리스트. pypdfium2 우선(poppler 불필요·오프라인),
    실패 시 pdf2image(poppler) 폴백."""
    # 1) pypdfium2 (시스템 의존성 없음)
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(data)
        out = []
        scale = dpi / 72.0
        for i in range(min(len(pdf), max_pages)):
            bitmap = pdf[i].render(scale=scale)
            pil = bitmap.to_pil().convert("RGB")
            out.append(cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR))
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass
    # 2) 폴백: pdf2image + poppler
    try:
        from pdf2image import convert_from_bytes
        pages = convert_from_bytes(data, dpi=dpi, first_page=1, last_page=max_pages)
        return [cv2.cvtColor(np.array(pg), cv2.COLOR_RGB2BGR) for pg in pages]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("PDF 변환 실패 (pypdfium2/poppler 모두 불가)") from e


def encode_png_b64(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("PNG 인코딩 실패")
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode()


def render_overlay(image: np.ndarray, lines: list[TextLine],
                   regions: list[LayoutRegion], *,
                   show_text_boxes: bool = True,
                   show_regions: bool = True) -> np.ndarray:
    """원본 위에 레이아웃 영역(굵은 색박스)과 텍스트 박스(얇은 초록)를 그림."""
    vis = image.copy()
    if show_regions:
        for rg in regions:
            b = rg.bbox
            color = _REGION_COLORS.get(rg.label, _REGION_COLORS["other"])
            cv2.rectangle(vis, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)),
                          color, 2)
            cv2.putText(vis, rg.label, (int(b.x1), max(0, int(b.y1) - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    if show_text_boxes:
        for ln in lines:
            pts = np.array(ln.polygon, dtype=np.int32).reshape(-1, 1, 2)
            # VLM 결과는 좌표가 없어 폴리곤이 퇴화(전부 0,0)한다 — 점 찍힘 방지로 스킵
            xs, ys = pts[:, 0, 0], pts[:, 0, 1]
            if xs.max() - xs.min() < 2 or ys.max() - ys.min() < 2:
                continue
            cv2.polylines(vis, [pts], isClosed=True, color=(0, 200, 0),
                          thickness=1, lineType=cv2.LINE_AA)
    return vis
