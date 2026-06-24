#!/usr/bin/env bash
# 도장 억제 on/off 측정 — 엔진별 별도 프로세스(GPU 컨텍스트 격리).
cd /home/siwon/projects/ocr || exit 1
for eng in paddle hybrid_vl paddle_vl; do
  echo "===== [$(date +%T)] SEAL 측정: $eng ====="
  MEAS_ENGINES="$eng" OCR_PADDLE_GPU=force ./.venv/bin/python -u scripts/measure_all.py seal 2>&1 \
    | grep -aE "SEAL |Traceback|Error|비가용"
done
echo "===== [$(date +%T)] SEAL 측정 완료 ====="
