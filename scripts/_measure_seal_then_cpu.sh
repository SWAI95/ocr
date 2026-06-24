#!/usr/bin/env bash
# GPU 측정 후 실행: 도장 억제(엔진별 격리) → CPU 측정(doc_00 전 엔진, 한 프로세스).
cd /home/siwon/projects/ocr || exit 1

echo "##### SEAL 단계 시작 $(date +%T) #####"
for eng in paddle hybrid_vl paddle_vl; do
  echo "===== [$(date +%T)] SEAL: $eng ====="
  MEAS_ENGINES="$eng" OCR_PADDLE_GPU=force ./.venv/bin/python -u scripts/measure_all.py seal 2>&1 \
    | grep -aE "SEAL |Traceback|Error|비가용"
done

echo "##### CPU 단계 시작 $(date +%T) (오래 걸림: VLM CPU ~9분/page) #####"
./.venv/bin/python -u scripts/measure_all.py cpu 2>&1 | grep -aE "CPU |Traceback|Error|비가용"

echo "##### 측정 전체 완료 $(date +%T) #####"
