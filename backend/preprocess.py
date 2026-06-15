"""문서 영상 전처리 — 글자만 깨끗하게, 노이즈(도장/바코드/워터마크) 제거.

OCR 입력 직전에 적용. 각 단계는 토글 가능(EngineOptions.preprocess).
원칙: 글자 보존, 빨간 도장·바코드·연한 워터마크 같은 비텍스트 노이즈 억제.
PaddleOCR 등은 자연 이미지 학습이라 과한 이진화는 오히려 해로울 수 있어,
기본은 '온건한' 보정(배경 평탄화)만 켜고 나머지는 선택.

스텝 id: "red_stamp"(빨간 도장 억제) · "flatten"(배경/워터마크 평탄화) ·
         "barcode"(바코드 마스킹) · "denoise"(약한 노이즈 제거)
"""
from __future__ import annotations

import cv2
import numpy as np

# 기본은 no-op (실측 결과 전처리 효과가 미미하고 일부는 해로움 — 문서별로
# 웹/CLI 에서 명시적으로 켜서 실험). 안전 후보: ["flatten"].
DEFAULT_STEPS: list[str] = []


def suppress_red_stamp(img: np.ndarray, keep_text: bool = True) -> np.ndarray:
    """빨간 도장/인장을 배경색(흰색)으로 치환. 글자(검정)는 보존.

    도장이 글자를 덮은 경우, 빨강만 지우면 아래 검은 글자가 드러난다.
    단 글자 위 빨강을 지우면 흰 구멍이 생기므로, 빨강 영역 중 '어두운(글자)'
    픽셀은 검정으로 복원해 글자를 살린다.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, (0, 60, 40), (12, 255, 255))
    m2 = cv2.inRange(hsv, (168, 60, 40), (180, 255, 255))
    red = cv2.bitwise_or(m1, m2)
    out = img.copy()
    out[red > 0] = (255, 255, 255)
    if keep_text:
        # 빨강 영역에서 원래 어두웠던(글자 가능성) 픽셀은 검정 복원
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        dark = (gray < 90) & (red > 0)
        out[dark] = (0, 0, 0)
    return out


def flatten_background(img: np.ndarray, ksize: int = 41) -> np.ndarray:
    """조명/워터마크 평탄화 — 큰 커널 배경으로 나눠 균일 흰 배경 + 글자 대비↑.

    연한 워터마크 로고·그림자·그라데이션을 억제하고 옅은 글자를 또렷하게.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    k = ksize if ksize % 2 == 1 else ksize + 1
    bg = cv2.medianBlur(gray, k)
    bg = np.where(bg == 0, 1, bg).astype(np.uint8)
    norm = cv2.divide(gray, bg, scale=255)
    return cv2.cvtColor(norm, cv2.COLOR_GRAY2BGR)


def mask_barcodes(img: np.ndarray) -> tuple[np.ndarray, list[list[int]]]:
    """바코드(촘촘한 세로 막대 영역) 감지 후 흰색 마스킹. (이미지, 박스들) 반환."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gx = cv2.convertScaleAbs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=-1))
    gy = cv2.convertScaleAbs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=-1))
    grad = cv2.subtract(gx, gy)  # 세로 에지 우세 영역 강조
    grad = cv2.blur(grad, (9, 9))
    _, th = cv2.threshold(grad, 120, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (23, 7))
    closed = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
    closed = cv2.erode(closed, None, iterations=4)
    closed = cv2.dilate(closed, None, iterations=4)
    cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = img.copy()
    boxes = []
    H, W = gray.shape
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        ar = w / float(h + 1)
        if w * h > 0.0008 * H * W and ar > 1.6 and w > 0.06 * W:
            out[max(0, y - 3):y + h + 3, max(0, x - 3):x + w + 3] = (255, 255, 255)
            boxes.append([x, y, x + w, y + h])
    return out, boxes


def denoise(img: np.ndarray) -> np.ndarray:
    return cv2.fastNlMeansDenoisingColored(img, None, 5, 5, 7, 21)


def preprocess(img: np.ndarray, steps: list[str] | None = None) -> tuple[np.ndarray, dict]:
    """steps 순서대로 적용. (결과이미지, 메타) 반환."""
    steps = DEFAULT_STEPS if steps is None else steps
    meta: dict = {"steps": list(steps), "barcode_boxes": []}
    out = img
    for s in steps:
        if s == "red_stamp":
            out = suppress_red_stamp(out)
        elif s == "flatten":
            out = flatten_background(out)
        elif s == "barcode":
            out, boxes = mask_barcodes(out)
            meta["barcode_boxes"] = boxes
        elif s == "denoise":
            out = denoise(out)
    return out, meta
