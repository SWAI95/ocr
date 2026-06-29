---
name: deploy-bundle
description: 내부망(오프라인) 반입용 git bundle 릴리스를 만든다 — pull → 직전 태그와 변경점 비교 → 새 버전 태그 생성 → 태그/브랜치 푸시 → 변경점을 vX.Y.Z.bundle 로 패키징. "배포하자", "배포해", "deploy", "릴리스 만들어", "번들 만들어", "내부망에 가져갈 거 만들어" 같은 요청에서 사용한다.
---

# deploy-bundle — 내부망 반입용 릴리스 번들

온라인 빌드 머신에서 변경점을 **git bundle** 한 덩이로 묶어, 사용자가 USB 등으로
내부망에 반입한 뒤 내부망 git 에 직접 병합한다. 결정적 동작은 모두
`scripts/deploy_bundle.sh` 에 있다.

## 가드레일 (먼저 확인)

- **푸시·태그 푸시는 부작용 작업** → 사용자가 "배포해/푸시해"로 **명시 지시할 때만** 실행한다.
  확신이 없으면 푸시 전에 한 번 확인한다. 확인 없이 자동 푸시 금지.
- 태그는 커밋을 가리킨다 → **배포할 변경은 반드시 먼저 커밋**되어 있어야 한다.

## 워크플로

1. **동기화**: `git pull --ff-only origin <branch>` 로 원격과 맞춘다(분기되면 중단·정리 안내).
2. **변경 커밋**: 미커밋 변경이 있으면 내용을 요약해 커밋 메시지를 제안하고 **사용자 승인 후** 커밋.
   (작업트리가 깨끗해야 다음 단계로 간다.)
3. **버전 결정**: `git describe --tags --abbrev=0` 로 직전 태그를 보고,
   `git log <직전태그>..HEAD` 변경 성격으로 새 버전(`vMAJOR.MINOR.PATCH`)을 **사용자와 확정**.
   - 문서/사소 수정 → PATCH(+0.0.1) · 기능 추가 → MINOR · 호환성 깨짐 → MAJOR.
4. **실행**: `scripts/deploy_bundle.sh <새버전>` (clean 확인 → pull → 변경점 표시 →
   annotated 태그 → 태그+브랜치 푸시 → `dist/bundles/<버전>.bundle` 생성 → `git bundle verify`).
   - 푸시를 미루려면 `--no-push`, 내부망에 베이스 태그가 없으면 `--full`.
5. **인계**: 생성된 `dist/bundles/<버전>.bundle` 경로와 내부망 적용 명령(아래)을 사용자에게 알린다.

## 번들 방식

- **기본(증분)**: 직전 태그 `^PREV` 를 전제로, `PREV..새태그` 의 변경만 담는다(작다).
  내부망 repo 에 직전 태그가 있어야 적용된다.
- **`--full`**: `git bundle create --all` — 내부망에 베이스가 없거나 최초 반입일 때.
  직전 태그가 없으면 스크립트가 자동으로 full 로 전환한다.

## 내부망에서 적용 (반입 후, 오프라인)

```bash
cd <내부망 repo>
git bundle verify <버전>.bundle                       # 전제 커밋 존재 확인
git pull <버전>.bundle <branch>                       # 브랜치로 fast-forward 병합
git fetch <버전>.bundle 'refs/tags/*:refs/tags/*'     # 태그도 가져오기
```

`verify` 가 prerequisite 누락을 보고하면 내부망에 직전 태그가 없는 것 → 빌드 머신에서
`scripts/deploy_bundle.sh <버전> --full` 로 다시 만들어 반입한다.

## 참고

- 출력물 `dist/bundles/` 는 빌드 산출물이므로 `.gitignore` 에 둔다(커밋하지 않음).
- 스크립트 도움말: `scripts/deploy_bundle.sh --help`.
