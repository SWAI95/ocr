"""스모크 테스트 — 합성 한글 이미지를 만들어 엔진 파이프라인을 검증.

오프라인 검증:
    OCR_OFFLINE=1 python scripts/smoke_test.py        # 로컬 모델만으로 동작해야 함
엔진 지정:
    python scripts/smoke_test.py --engine paddle
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.engines.base import EngineOptions  # noqa: E402
from backend.metrics.cer_wer import score  # noqa: E402
from backend.pipeline import runner  # noqa: E402

GT = "계약서 제1조 (목적) 본 계약의 목적은 OCR 성능 검증이다 ABC 123"

_FONT_CANDIDATES = [
    str(config.SAMPLES_DIR / "fonts" / "NanumGothic.ttf"),
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def make_image() -> tuple[np.ndarray, str]:
    import cv2
    from PIL import Image, ImageDraw, ImageFont
    font_path = next((f for f in _FONT_CANDIDATES if Path(f).exists()), None)
    img = Image.new("RGB", (900, 160), "white")
    d = ImageDraw.Draw(img)
    if font_path:
        font = ImageFont.truetype(font_path, 36)
        d.text((20, 30), GT, fill="black", font=font)
        gt = GT
    else:
        gt = "OCR smoke test ABC 123"
        d.text((20, 60), gt, fill="black")
        print("  [!] 한글 폰트 없음 → 영문 텍스트로 대체 (apt install fonts-nanum 권장)")
    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    return bgr, gt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="paddle")
    ap.add_argument("--no-layout", action="store_true")
    args = ap.parse_args()

    print(f"[*] OFFLINE={config.OFFLINE} | engine={args.engine}")
    if not runner.is_available(args.engine):
        print(f"[!] 엔진 '{args.engine}' 미설치"); sys.exit(1)

    image, gt = make_image()
    sample_path = config.SAMPLES_DIR / "smoke.png"
    import cv2
    cv2.imwrite(str(sample_path), image)
    print(f"[*] 테스트 이미지: {sample_path}")

    opts = EngineOptions(lang="korean", use_gpu=True,
                         use_layout=not args.no_layout)
    result = runner.run(args.engine, image, opts)

    print(f"[*] device={result.device} | timings={result.timings_ms}")
    print(f"[*] 영역 {len(result.regions)}개 / 라인 {len(result.lines)}개")
    print("[*] 인식 결과:")
    for ln in result.lines:
        print(f"    ({ln.score:.2f}) {ln.text}")
    sc = score(gt, result.full_text)
    print(f"\n[=] CER={sc.cer:.3f}  WER={sc.wer:.3f}  문자정확도={sc.cer_accuracy*100:.1f}%")
    print("[OK] 스모크 테스트 완료")


if __name__ == "__main__":
    main()
