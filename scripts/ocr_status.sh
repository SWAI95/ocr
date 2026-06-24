#!/usr/bin/env bash
# OCR 9000 상태 1회 스냅샷 (온디맨드). "어디까지 갔나 / 멈췄나"를 즉시 확인.
cd /home/siwon/projects/ocr || exit 1
LOG=logs/ocr_server.log
echo "===== OCR 9000 상태  $(date '+%F %T') ====="
if ss -ltn 2>/dev/null | grep -q ":9000"; then
  echo "서버      : UP (9000 LISTEN)"
else
  echo "서버      : ★DOWN★"
fi
echo "활성 연결 : $(ss -tn state established 2>/dev/null | grep -c ':9000')개 (요청 진행 중이면 >=1)"
last=$(grep -a "\[web\]" "$LOG" 2>/dev/null | tail -1)
mtime=$(stat -c%Y "$LOG" 2>/dev/null || echo 0); now=$(date +%s); idle=$((now - mtime))
if [ -z "$last" ] || echo "$last" | grep -qE "완료 ==|compare 완료"; then
  echo "진행 상태 : IDLE (대기 — 마지막 요청 완료됨)"
else
  echo "진행 상태 : ▶ 진행 중/미완료 (로그 ${idle}초 전 갱신)"
  [ "$idle" -gt 180 ] && echo "            ⚠ ${idle}초간 무변화 — 스톨 의심"
fi
echo "마지막    : ${last:-(요청 기록 없음)}"
echo "GPU       : $(nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null | head -1)"
if [ -f logs/status.json ]; then echo "모니터    : $(cat logs/status.json)"; fi
echo "--- 최근 진행 6줄 ---"
grep -a "\[web\]" "$LOG" 2>/dev/null | tail -6 | sed 's/^/  /'
