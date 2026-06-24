#!/usr/bin/env bash
# 9000 OCR 서버 스톨 감시. logs/ocr_server.log 가 '요청 진행 중'인데도 STALL_SEC 동안
# 안 늘면 멈춤으로 보고 종료(상위 에이전트에 알림). 진행 중 판단: 마지막 [web] 줄이
# '완료 ==' 경계가 아니면 진행 중. 콜드 모델 로드(최대 ~90s) 오탐 방지로 임계 넉넉히.
cd /home/siwon/projects/ocr || exit 1
LOG=logs/ocr_server.log
STALL_SEC=${STALL_SEC:-180}
MAX_SEC=${MAX_SEC:-7200}
start=$(date +%s)
last_size=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
last_change=$(date +%s)
echo "[watchdog] 시작 STALL=${STALL_SEC}s MAX=${MAX_SEC}s"
while true; do
  sleep 10
  now=$(date +%s)
  cur=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
  if [ "$cur" != "$last_size" ]; then last_size=$cur; last_change=$now; fi
  # 포트 죽었나
  if ! ss -ltn 2>/dev/null | grep -q ":9000"; then
    echo "[watchdog] !!! 서버 9000 포트 다운 — 종료"; exit 3
  fi
  lastweb=$(grep -a "\[web\]" "$LOG" 2>/dev/null | tail -1)
  if echo "$lastweb" | grep -qE "완료 ==|compare 완료|watchdog"; then
    inflight=0
  else
    inflight=1
  fi
  idle=$((now - last_change))
  if [ "$inflight" = 1 ] && [ "$idle" -gt "$STALL_SEC" ]; then
    echo "[watchdog] !!! 스톨 감지: ${idle}s 동안 로그 무변화"
    echo "[watchdog] 마지막 진행: $lastweb"
    exit 2
  fi
  if [ $((now - start)) -gt "$MAX_SEC" ]; then
    echo "[watchdog] 정상 종료(감시 시간 만료, 스톨 없음)"; exit 0
  fi
done
