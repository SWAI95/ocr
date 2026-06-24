"""PPTX용 시각화 이미지 생성 → data/pptx_assets/*.png.

- region_overlay.png : 영역 구분(표/제목/머리글/그림/도장) 오버레이 (웹 시각화와 동일 룩)
- doc04_stamp_before/after.png : 도장이 글자 가린 페이지 원본 vs 빨강 억제 적용본
- thumb_doc00/04/05.png : 샘플 문서 대표 페이지 썸네일

재사용: backend.imaging(decode_image/render_overlay), backend.preprocess(suppress_red_stamp),
        PaddleEngine(레이아웃+영역).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from backend.engines.base import EngineOptions  # noqa: E402
from backend.engines.paddle_engine import PaddleEngine  # noqa: E402
from backend.imaging import decode_image, render_overlay  # noqa: E402
from backend.preprocess import suppress_red_stamp  # noqa: E402

GTD = Path("samples/dataset/korean_docs")
OUT = Path("data/pptx_assets")
OUT.mkdir(parents=True, exist_ok=True)


def pages(stem_file: str):
    p = GTD / stem_file
    return decode_image(p.read_bytes(), p.name)


def red_pixels(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.bitwise_or(cv2.inRange(hsv, (0, 80, 60), (10, 255, 255)),
                       cv2.inRange(hsv, (170, 80, 60), (180, 255, 255)))
    return int(np.count_nonzero(m))


def save(name, img):
    cv2.imwrite(str(OUT / name), img)
    print(f"  저장 {name}  {img.shape[1]}x{img.shape[0]}")


def main():
    eng = PaddleEngine()
    opts = EngineOptions(lang="korean", use_gpu=True, use_layout=True, use_table=True)

    # --- A. 영역 구분 오버레이 (doc_00 page0: 폼+제목+표) ---
    print("[A] 영역 오버레이")
    img = pages("doc_00.pdf")[0]
    res = eng.run(img, opts)
    labels = sorted({r.label for r in res.regions})
    print("  영역 라벨:", labels, " 영역수:", len(res.regions))
    save("region_overlay.png", render_overlay(img, res.lines, res.regions))
    save("thumb_doc00.png", img)

    # --- B. 도장 페이지 before/after (doc_04 에서 빨강 최다 페이지) ---
    print("[B] 도장 before/after (doc_04)")
    d04 = pages("doc_04.pdf")
    reds = [(i, red_pixels(p)) for i, p in enumerate(d04)]
    si = max(reds, key=lambda x: x[1])[0]
    print("  도장 페이지 추정:", si, " 빨강픽셀:", dict(reds))
    before = d04[si]
    after = suppress_red_stamp(before)
    save("doc04_stamp_before.png", before)
    save("doc04_stamp_after.png", after)
    save("thumb_doc04.png", d04[0])

    # --- C. doc_05 썸네일 ---
    save("thumb_doc05.png", pages("doc_05.pdf")[0])

    print("[OK] 자산 생성 완료:", OUT)


if __name__ == "__main__":
    main()
