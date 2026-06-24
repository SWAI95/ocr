#!/usr/bin/env bash
# GPU 측정을 엔진별 '별도 프로세스'로 — paddle 선로드 후 VLM 이 CPU 폴백하는 문제 회피.
cd /home/siwon/projects/ocr || exit 1
for eng in paddle paddle_vl hybrid_vl ollama_vl easyocr tesseract; do
  echo "===== [$(date +%T)] GPU 측정: $eng ====="
  MEAS_ENGINES="$eng" OCR_PADDLE_GPU=force ./.venv/bin/python -u scripts/measure_all.py gpu 2>&1 \
    | grep -aE "GPU |Traceback|Error|비가용"
done
echo "===== [$(date +%T)] GPU 측정 전체 완료 ====="
