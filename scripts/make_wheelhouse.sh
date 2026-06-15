#!/usr/bin/env bash
# 빌드 머신(인터넷 O)에서 실행 → 내부망 반입용 wheelhouse 생성.
# 전제: 내부망과 동일 아키텍처/Python (linux x86_64, Python 3.12)에서 실행.
#
# 배포 4090 통일을 위해 torch·paddle 모두 cu126 인덱스에서 받는다.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PY:-./.venv/bin/python}"
REQ="${REQ:-requirements/deploy.txt}"
OUT="${OUT:-wheelhouse}"

[[ -f "$REQ" ]] || { echo "[!] $REQ 없음"; exit 1; }
mkdir -p "$OUT"
echo "[*] wheelhouse 다운로드 → $OUT  (requirements: $REQ)"

# 세 인덱스: PyPI + PyTorch(cu126) + PaddlePaddle(cu126)
"$PY" -m pip download -r "$REQ" -d "$OUT" \
  --index-url https://pypi.org/simple \
  --extra-index-url https://download.pytorch.org/whl/cu126 \
  --extra-index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/

echo "[*] 받은 휠: $(ls -1 "$OUT" | wc -l) 개 / $(du -sh "$OUT" | cut -f1)"
echo "[OK] 내부망 반입: wheelhouse/ + requirements/deploy.txt + models/ + 코드"
