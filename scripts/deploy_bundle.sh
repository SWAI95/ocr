#!/usr/bin/env bash
# deploy_bundle.sh — 내부망 반입용 릴리스 번들 생성
#
# 흐름: 작업트리 클린 확인 → pull(ff-only) → 직전 태그와 변경점 비교 →
#       새 태그 생성 → (태그+브랜치) 푸시 → 변경점을 git bundle 로 패키징 → 검증.
# 결과물: <OUT_DIR>/<version>.bundle  (예: dist/bundles/v1.1.1.bundle)
#
# 사용법:
#   scripts/deploy_bundle.sh v1.1.1                 # 증분 번들(직전 태그..새 태그) + 푸시
#   scripts/deploy_bundle.sh v1.1.1 --no-push       # 태그/번들만, 원격 푸시 안 함
#   scripts/deploy_bundle.sh v1.1.1 --full          # 전체 번들(--all, 내부망에 베이스 없을 때)
#   scripts/deploy_bundle.sh v1.1.1 --out /tmp/rel  # 출력 디렉토리 지정
#   scripts/deploy_bundle.sh v1.1.1 --remote origin # 원격 이름(기본 origin)
#
# 커밋은 이 스크립트가 하지 않는다 — 배포할 변경은 먼저 커밋되어 있어야 한다.
set -euo pipefail

VER=""; MODE="incremental"; DO_PUSH=1; OUT_DIR="dist/bundles"; REMOTE="origin"
while [ $# -gt 0 ]; do
  case "$1" in
    --full)     MODE="full"; shift ;;
    --no-push)  DO_PUSH=0; shift ;;
    --out)      OUT_DIR="${2:?}"; shift 2 ;;
    --remote)   REMOTE="${2:?}"; shift 2 ;;
    -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
    v*|[0-9]*)  VER="$1"; shift ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
done

[ -n "$VER" ] || { echo "ERROR: 새 버전을 인자로 주세요. 예: $0 v1.1.1" >&2; exit 2; }
case "$VER" in v*) ;; *) VER="v$VER" ;; esac   # 'v' 접두사 자동 보정

cd "$(git rev-parse --show-toplevel)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

# 1) 작업트리 클린 확인 (태그는 커밋을 가리키므로 미커밋 변경이 있으면 중단)
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: 커밋되지 않은 변경이 있습니다. 먼저 커밋한 뒤 다시 실행하세요." >&2
  git status --short >&2
  exit 1
fi

# 2) 새 태그가 이미 있는지 확인
if git rev-parse -q --verify "refs/tags/$VER" >/dev/null; then
  echo "ERROR: 태그 $VER 가 이미 존재합니다." >&2; exit 1
fi

# 3) pull (fast-forward 만 — 분기되면 중단하고 사용자가 정리)
echo ">> git pull --ff-only $REMOTE $BRANCH"
git pull --ff-only "$REMOTE" "$BRANCH"

# 4) 직전 태그 + 변경점 표시
PREV="$(git describe --tags --abbrev=0 2>/dev/null || true)"
echo
echo "==================== 릴리스 미리보기 ===================="
echo " 직전 태그 : ${PREV:-(없음 — 전체 번들로 생성)}"
echo " 새 태그   : $VER"
echo " 브랜치    : $BRANCH @ $(git rev-parse --short HEAD)"
echo "---------------------------------------------------------"
if [ -n "$PREV" ]; then
  echo " 커밋:"; git log --oneline "$PREV"..HEAD || true
  echo " 변경 파일:"; git diff --stat "$PREV"..HEAD || true
else
  MODE="full"
fi
echo "========================================================="
echo

# 5) annotated 태그 생성
git tag -a "$VER" -m "release $VER"
echo ">> 태그 생성: $VER"

# 6) 푸시 (브랜치 + 태그)
if [ "$DO_PUSH" -eq 1 ]; then
  echo ">> git push $REMOTE $BRANCH && push tag $VER"
  git push "$REMOTE" "$BRANCH"
  git push "$REMOTE" "$VER"
else
  echo ">> --no-push: 원격 푸시 건너뜀 (수동 푸시: git push $REMOTE $BRANCH && git push $REMOTE $VER)"
fi

# 7) 번들 생성
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/$VER.bundle"
if [ "$MODE" = "full" ]; then
  echo ">> 전체 번들 생성 (--all): $OUT"
  git bundle create "$OUT" --all
else
  echo ">> 증분 번들 생성 ($PREV..$VER): $OUT"
  # ^PREV = 직전 태그를 전제(prerequisite)로 → 내부망 repo 에 PREV 가 있어야 적용됨
  git bundle create "$OUT" "^$PREV" "$VER" "$BRANCH"
fi

# 8) 검증 + 내부망 적용 안내
echo
echo ">> 번들 검증:"
git bundle verify "$OUT"
SZ="$(du -h "$OUT" | cut -f1)"
echo
echo "✅ 완료: $OUT ($SZ)"
echo
echo "── 내부망에서 적용 (반입 후) ─────────────────────────────"
echo "  cd <내부망 repo>"
echo "  git bundle verify $VER.bundle           # 전제 커밋(${PREV:-없음})이 있는지 확인"
echo "  git pull $VER.bundle $BRANCH            # $BRANCH 로 병합(fast-forward)"
echo "  git fetch $VER.bundle 'refs/tags/*:refs/tags/*'   # 태그도 가져오기"
if [ "$MODE" != "full" ] && [ -n "$PREV" ]; then
  echo "  # ※ 'verify' 가 prerequisite 누락을 보고하면, 내부망에 $PREV 가 없는 것."
  echo "  #   그 경우 빌드 머신에서 --full 로 다시 만들어 반입하세요:"
  echo "  #     scripts/deploy_bundle.sh $VER --full --no-push"
fi
echo "──────────────────────────────────────────────────────────"
