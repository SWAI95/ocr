"""전역 설정 — 경로, 디바이스, 오프라인 모드.

내부망(오프라인) 배포 시 OCR_OFFLINE=1 로 두면 모든 엔진이 로컬 모델만
로드하도록 환경변수(HF_HUB_OFFLINE 등)를 강제한다. 개발(인터넷 가능)
환경에서는 0(기본)으로 두어 최초 1회 모델 다운로드를 허용한다.
"""
from __future__ import annotations

import os
from pathlib import Path

# PaddlePaddle 3.3.x 의 oneDNN(mkldnn) + PIR 실행기 버그 회피.
# CPU 추론에서 mkldnn 이 켜져 있으면 'ConvertPirAttribute2RuntimeAttribute
# not support' 로 실패한다. paddle import 이전에 전역으로 꺼 둔다(GPU 무관).
os.environ.setdefault("FLAGS_use_mkldnn", "0")

# --- 경로 ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = Path(os.environ.get("OCR_MODELS_DIR", PROJECT_ROOT / "models"))
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = DATA_DIR / "outputs"
SAMPLES_DIR = PROJECT_ROOT / "samples"

# 엔진별 모델 캐시 (오프라인 번들에 그대로 포함)
PADDLE_MODELS_DIR = MODELS_DIR / "paddle"   # PaddleX 인퍼런스 모델 디렉토리
EASYOCR_MODELS_DIR = MODELS_DIR / "easyocr"  # EasyOCR .pth 저장소
HF_CACHE_DIR = MODELS_DIR / "hf"             # HuggingFace 가중치 캐시(transformers 등)
TESSDATA_DIR = MODELS_DIR / "tessdata"       # *.traineddata

for _d in (MODELS_DIR, OUTPUT_DIR, PADDLE_MODELS_DIR, EASYOCR_MODELS_DIR,
           HF_CACHE_DIR, TESSDATA_DIR, SAMPLES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- 오프라인 모드 ---
OFFLINE = os.environ.get("OCR_OFFLINE", "0") == "1"


def apply_offline_env() -> None:
    """오프라인이면 외부 네트워크 다운로드를 환경변수로 차단하고
    번들된 로컬 모델 캐시만 사용하도록 강제.

    PaddleX 는 모델을 `<PADDLE_PDX_CACHE_HOME>/official_models/<name>` 에 둔다.
    캐시 홈을 번들 경로(PADDLE_MODELS_DIR)로 지정하고 소스 체크를 끄면,
    네트워크 없이 번들된 모델만으로 동작한다.
    """
    if not OFFLINE:
        return
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
    # PaddleX: 캐시 홈을 번들 경로로 + 모델 소스(네트워크) 연결 체크 비활성
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(PADDLE_MODELS_DIR))
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def hf_cache_setup() -> None:
    """오프라인 여부와 무관하게 HF 캐시를 프로젝트 내부로 고정."""
    os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))


# --- 디바이스 ---
def resolve_device(prefer_gpu: bool = True) -> str:
    """'gpu' 또는 'cpu'. GPU 가용성은 엔진별로 재확인하므로 여기선 선호값만."""
    if not prefer_gpu:
        return "cpu"
    if os.environ.get("OCR_FORCE_CPU", "0") == "1":
        return "cpu"
    return "gpu"


apply_offline_env()
hf_cache_setup()
