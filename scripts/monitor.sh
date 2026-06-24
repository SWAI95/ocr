#!/usr/bin/env bash
# OCR 9000 모니터 데몬 (테스트/개발과 분리된 '모니터링' 프로세스).
# 15초마다 서버 상태를 logs/monitor.log(이력) + logs/status.json(현재 스냅샷)에 기록.
# 요청 진행 중인데 STALL_SEC 동안 로그가 안 늘면 STALL, 포트 다운이면 DOWN 으로
# 판정하고 종료(상위 에이전트에 알림). 평상시엔 계속 돌며 하트비트만 남긴다.
cd /home/siwon/projects/ocr || exit 1
LOG=logs/ocr_server.log
MON=logs/monitor.log
STATUS=logs/status.json
STALL_SEC=${STALL_SEC:-180}      # 콜드 모델 로드(~90s) 오탐 방지로 넉넉히
MAX_SEC=${MAX_SEC:-14400}        # 4시간 후 정상 종료
mkdir -p logs
start=$(date +%s)
last_size=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
last_change=$(date +%s)
echo "[$(date '+%F %T')] [monitor] 시작 (STALL=${STALL_SEC}s, MAX=${MAX_SEC}s)" >> "$MON"
while true; do
  sleep 15
  now=$(date +%s)
  cur=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
  if [ "$cur" != "$last_size" ]; then last_size=$cur; last_change=$now; fi
  idle=$((now - last_change))
  up=0; ss -ltn 2>/dev/null | grep -q ":9000" && up=1
  last=$(grep -a "\[web\]" "$LOG" 2>/dev/null | tail -1)
  inflight=1
  if [ -z "$last" ] || echo "$last" | grep -qE "완료 ==|compare 완료"; then inflight=0; fi
  gpu=$(nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
  state="OK"
  [ "$up" = 0 ] && state="DOWN"
  if [ "$up" = 1 ] && [ "$inflight" = 1 ] && [ "$idle" -gt "$STALL_SEC" ]; then state="STALL"; fi
  esc_last=$(echo "$last" | sed 's/\\/\\\\/g; s/"/\\"/g')
  printf '{"ts":"%s","server_up":%d,"inflight":%d,"idle_sec":%d,"gpu":"%s","state":"%s","last":"%s"}\n' \
    "$(date '+%F %T')" "$up" "$inflight" "$idle" "$gpu" "$state" "$esc_last" > "$STATUS"
  echo "[$(date '+%F %T')] [monitor] state=$state up=$up inflight=$inflight idle=${idle}s gpu=${gpu} | ${last:-(요청 없음)}" >> "$MON"
  if [ "$state" = "STALL" ] || [ "$state" = "DOWN" ]; then
    echo "[$(date '+%F %T')] [monitor] !!! ALERT=$state — 종료(알림)" >> "$MON"
    echo "ALERT $state idle=${idle}s last=${last}"
    exit 2
  fi
  if [ $((now - start)) -gt "$MAX_SEC" ]; then
    echo "[$(date '+%F %T')] [monitor] 정상 종료(감시 만료)" >> "$MON"; exit 0
  fi
done
