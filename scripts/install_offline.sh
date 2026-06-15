#!/usr/bin/env bash
# 내부망(인터넷 X)에서 실행 → wheelhouse 로부터 완전 오프라인 설치.
set -euo pipefail
cd "$(dirname "$0")/.."

PY_BIN="${PY_BIN:-python3.12}"
OUT="wheelhouse"
REQ="${REQ:-requirements/deploy.txt}"

[[ -d "$OUT" ]] || { echo "[!] $OUT 없음 (빌드 머신에서 make_wheelhouse.sh로 생성 후 반입)"; exit 1; }
[[ -f "$REQ" ]] || { echo "[!] $REQ 없음"; exit 1; }

echo "[*] venv 생성"
"$PY_BIN" -m venv .venv
./.venv/bin/python -m pip install --no-index --find-links "$OUT" -U pip setuptools wheel

echo "[*] 오프라인 설치 (--no-index)"
./.venv/bin/python -m pip install --no-index --find-links "$OUT" -r "$REQ"

echo "[*] 시스템 의존성(tesseract/poppler)은 .deb 번들로 별도 설치:"
echo "      sudo dpkg -i debs/*.deb   (또는 apt-get install --no-download)"

echo "[OK] 오프라인 설치 완료. 다음:"
echo "      OCR_OFFLINE=1 ./.venv/bin/python scripts/smoke_test.py"
echo "      OCR_OFFLINE=1 ./.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000"
