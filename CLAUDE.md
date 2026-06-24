# CLAUDE.md — 오프라인 한국어 문서 OCR 엔진

내부망(오프라인) 배포용 한국어 문서 OCR 엔진 + 멀티엔진 비교 테스트 웹.
타겟: 인쇄 문서/계약서 PDF (한글+영어+숫자+기호, 표·각주·도장). 손글씨는 비대상.

## 작업 규칙 (사용자 지정)

- **테스트할 때는 항상 GPU 사용** (`EngineOptions(use_gpu=True, ...)`, batch_test 는
  기본 GPU. `--no-gpu` 쓰지 말 것).
  - paddle 은 **cu129 휠(CUDA 12.9)로 교체 후 이 개발 머신(RTX5090 Blackwell sm_120)
    에서도 GPU 동작**한다(측정: 0.4s/page). `_paddle_gpu_usable()` 가 작은 conv 결과가
    0 이 아닌지 런타임 프로브로 자동 판정 — 지원 휠이면 GPU, 아니면 CPU 폴백.
    설치: `pip install "paddlepaddle-gpu==3.3.1" --no-deps --force-reinstall
    --index-url https://www.paddlepaddle.org.cn/packages/stable/cu129/`.
    (예전 cu126 휠은 Blackwell 에서 conv 가 '조용히 0' 반환 → CPU 폴백했음.
    배포 4090(sm_89)은 cu126·cu129 둘 다 GPU. torch 는 --no-deps 로 cu128 보존.)
  - easyocr(torch cu128), ollama_vl(Ollama 자체 CUDA)은 5090 에서도 GPU 사용.
- **문서 OCR 은 반드시 레이아웃 ON** (`use_table=True` → PPStructureV3).
  레이아웃 OFF 면 큰 이미지가 다운스케일돼 조밀 단락이 깨진다(doc_01 41.6% → ON 1.4%).

## 핵심 명령

```bash
# 서버 (웹: http://localhost:8000)
./.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
# 일괄 테스트 (문서는 --table 필수)
./.venv/bin/python scripts/batch_test.py <폴더> --engine paddle --table
# 오프라인 검증
OCR_OFFLINE=1 ./.venv/bin/python scripts/smoke_test.py
```

## 엔진 (벤치)
paddle(Primary, PP-StructureV3) · **hybrid_vl(페이지 레이아웃으로 분류 → 폼·표=Paddle /
줄글=Qwen VLM 라우팅; 줄바꿈 단락 WER 복구)** · paddle_vl(PaddleOCR-VL 0.9B,
**5090·4090 GPU 모두 동작** — cu129 + markdown 접근자 수정 후 5090 정상) ·
ollama_vl(로컬 Ollama VLM, 5090 GPU) · easyocr · tesseract(sudo 설치 필요).

- **후처리 정규화(`postprocess.normalize_ocr_text`, runner.run 일괄 적용)**: ① 숫자 앞
  단독 'W'→'₩'(원화기호 오인식, KRW·MW 보호) ② VLM 의 리터럴 '\n' 아티팩트 제거.
  끄기 `OCR_NORMALIZE=off`. (실측 doc_00_p1 3.0→2.6%.)
- **paddle_vl 실측(2026-06)**: 평균 CER 3.9%(paddle 3.1%)지만 reading-order 깨진 폼/줄글
  페이지엔 압도적(doc_05_p9 19.6→3.3%, doc_01_p0 15.9→7.8%). 표-heavy/헤더 페이지엔 약함
  (표가 `<table>` 로 빠져 라인 누락). rec 모델 교체 불가: PP-OCRv5_server_rec 는 한글
  CER 90%, korean_PP-OCRv5_mobile_rec 가 offline 최선. 워스트 페이지 개선 레버 = VLM 라우팅.
- **hybrid VLM 백엔드 선택(`OCR_HYBRID_VLM`)**: `auto`(기본 — Ollama 떠 있으면 Qwen,
  아니면 paddle_vl 폴백) | `ollama_vl`(Qwen) | `paddle_vl`(in-process, **완전 offline**).
  **paddle_vl 백엔드로 hybrid 평균 CER 2.3%**(paddle 3.1%, paddle_vl 단독 3.9% 보다 우수)
  — Ollama 없이도 reading-order 복원. 병합이 해로운 유일 케이스는 Paddle 순서가 심하게
  깨진 doc_05_p9(병합 12.2% vs paddle_vl 단독 3.3%) — 1페이지뿐이라 always-merge 가 순이득.
  (개선 여지: sim(paddle,vlm) 낮으면 병합 스킵하는 게이트, 효과 ~0.3%/avg 라 보류.)

- **hybrid_vl 병합(항상)**: 줄글→Qwen 경로에서 **매 줄글 페이지를 항상** 'Paddle 글자(정확)
  + Qwen 띄어쓰기(가독성)'로 문자 단위 병합한다(`_merge_paddle_chars_qwen_spacing`). 과거엔
  큰 누락(`hole>=_HOLE_THRESHOLD=35`)일 때만 병합 → Qwen 비결정 롤에 따라 CER 1.5~2.2 출렁임.
  **항상 병합**으로 결정론적 Paddle 글자에 기대 **CER ~1.64 안정화**(2회 동일), WER 도 Qwen
  띄어쓰기로 유지. 문서 실제 띄어쓰기는 이미지를 읽은 Qwen 만 알기에 알고리즘 spacer(kiwi
  등)로 대체 불가. 비용: 줄글 페이지마다 Paddle 참조 1회(cu129 적용 후 5090 도 GPU ~0.4s).
  끄려면 `OCR_HYBRID_SAFETY=off`. Qwen 재현성은 `OCR_OLLAMA_SEED`(기본 0, temp 0)로 고정 —
  단 Ollama 서버 상태에 따라 세션 간 미세 변동 가능. `hole` 은 이제 진단용 메타로만 기록.
  (doc_00 실측: 전부Qwen 2.80/13.4 · 전부Paddle 2.28/43.2 · **항상병합 1.64/14.5**)

## 함정 (반복 실수 방지)
- **`pkill -f "uvicorn ..."` self-match**: kill 명령과 같은 줄에 "uvicorn" 문자열
  (서버 실행 등)이 있으면 pkill 이 자기 셸을 죽인다(exit 144). 반드시 **kill 은
  단독 줄**에서 `pkill -f "[u]vicorn backend.app"` (브라켓 트릭)으로.
- 서버 재시작은 background(run_in_background)로 띄우고, kill 은 별도 호출.
- Ollama 는 Windows 설치(`ollama.exe`, WSL interop). API 접근 위해 0.0.0.0 바인딩
  필요: `OLLAMA_HOST=0.0.0.0` (영구화는 Windows 환경변수).

상세: `DESIGN.md`, `README.md`. 진행 메모: `~/.claude/.../memory/` (있으면).
