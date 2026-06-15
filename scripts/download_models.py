"""빌드 머신(인터넷 O)에서 모델을 받아 models/ 에 수집 → 오프라인 번들 포함.

전략: PaddleX 캐시 홈을 프로젝트 내부(models/paddle)로 지정한 뒤 파이프라인을
인스턴스화하면, 모든 모델이 `models/paddle/official_models/<name>` 에 직접
다운로드된다. 이미 ~/.paddlex 에 받아 둔 게 있으면 먼저 복사해 재다운로드를
피한다. 내부망 배포 시 OCR_OFFLINE=1 이 같은 경로를 캐시 홈으로 잡는다.

사용:
    python scripts/download_models.py            # paddle(텍스트+레이아웃+표+도장) + easyocr
    python scripts/download_models.py --with-formula   # 수식/차트 모델까지
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import config  # noqa: E402

# PaddleX 캐시 홈을 번들 경로로 — paddle import/사용 전에 설정해야 함
PADDLE_CACHE_HOME = config.PADDLE_MODELS_DIR  # models/paddle
os.environ["PADDLE_PDX_CACHE_HOME"] = str(PADDLE_CACHE_HOME)
OFFICIAL = PADDLE_CACHE_HOME / "official_models"


def _seed_from_user_cache() -> None:
    """이미 ~/.paddlex/official_models 에 받아 둔 모델을 번들로 복사(재다운로드 회피)."""
    src = Path.home() / ".paddlex" / "official_models"
    if not src.exists():
        return
    OFFICIAL.mkdir(parents=True, exist_ok=True)
    copied = 0
    for d in src.iterdir():
        dst = OFFICIAL / d.name
        if d.is_dir() and not dst.exists():
            shutil.copytree(d, dst)
            copied += 1
    print(f"[*] 기존 캐시에서 복사: {copied}개 → {OFFICIAL}")


def download_paddle(with_formula: bool) -> None:
    print("[*] PaddleOCR 모델 수집 (캐시 홈:", PADDLE_CACHE_HOME, ")")
    _seed_from_user_cache()
    # 인스턴스화 → 누락 모델은 캐시 홈으로 다운로드
    from paddleocr import PaddleOCR, LayoutDetection, PPStructureV3
    from backend.models.registry import default_model
    rec = default_model("paddle", "recognition")
    det = default_model("paddle", "detection")
    layout = default_model("paddle", "layout")
    print(f"  - OCR: det={det}, rec={rec}")
    PaddleOCR(lang="korean", text_detection_model_name=det,
              text_recognition_model_name=rec, device="cpu", enable_mkldnn=False,
              use_doc_orientation_classify=False, use_doc_unwarping=False)
    print(f"  - Layout: {layout}")
    LayoutDetection(model_name=layout, device="cpu", enable_mkldnn=False)
    print("  - PPStructureV3 (표+도장, 수식/차트 %s)" % ("포함" if with_formula else "제외"))
    PPStructureV3(device="cpu", enable_mkldnn=False,
                  use_doc_orientation_classify=False, use_doc_unwarping=False,
                  use_textline_orientation=False,
                  use_formula_recognition=with_formula,
                  use_chart_recognition=with_formula,
                  text_recognition_model_name=rec, layout_detection_model_name=layout)
    n = len(list(OFFICIAL.iterdir())) if OFFICIAL.exists() else 0
    size = sum(f.stat().st_size for f in OFFICIAL.rglob("*") if f.is_file()) / 1e9
    print(f"  [+] official_models: {n}개 모델, {size:.2f} GB")


def download_easyocr() -> None:
    print("[*] EasyOCR 모델 수집 (ko+en) →", config.EASYOCR_MODELS_DIR)
    try:
        import easyocr
        easyocr.Reader(["ko", "en"], gpu=False,
                       model_storage_directory=str(config.EASYOCR_MODELS_DIR),
                       download_enabled=True)
        print("  [+] 완료")
    except Exception as e:  # noqa: BLE001
        print(f"  [!] EasyOCR 오류: {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-formula", action="store_true",
                    help="수식/차트 모델까지 포함(번들 크기 증가)")
    ap.add_argument("--skip-easyocr", action="store_true")
    args = ap.parse_args()
    download_paddle(args.with_formula)
    if not args.skip_easyocr:
        download_easyocr()
    print("\n[OK] 번들 대상: models/  (PADDLE_PDX_CACHE_HOME=models/paddle)")
    print("     내부망에서 OCR_OFFLINE=1 로 실행하면 이 모델들만 로드한다.")


if __name__ == "__main__":
    main()
