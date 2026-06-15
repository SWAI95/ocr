# 오프라인 한국어 문서 OCR 엔진 — 설계 문서

## 0. 목표 / 제약

**목표**: 한글+영어+숫자+기호가 섞인 실문서(계약서, 표·그래프·각주 포함, 도장·글자
겹침)를 ① 객체 인식으로 영역을 구분하고 ② 글자를 인식해 구조화 결과(JSON)로
반환하는 OCR 엔진. 더불어 **여러 OCR 엔진/모델을 바꿔가며 비교**하고 인식률
(CER/WER)을 확인하는 테스트 웹.

**핵심 제약**
- **완전 오프라인 (내부망)**: 외부 API 호출 금지. OCR은 *모델 다운로드 → 로컬 로드*
  방식만 허용. 런타임 자동 다운로드도 차단.
- **Detection / Recognition 모델을 각각 다운로드·교체** 가능해야 함.
- 패키지·모델 모두 인터넷 되는 빌드 머신에서 받아 내부망으로 반입.

**환경**
| | 개발(현재) | 배포(내부망) |
|---|---|---|
| OS | Ubuntu 24.04 (WSL2) | Ubuntu (이식) |
| Python | 3.12.3 | 3.12 |
| GPU | RTX 5090 (Blackwell, sm_120) | RTX 4090 (Ada, sm_89) |
| CUDA(드라이버) | 12.8 | 12.9 |

> 이식성: 배포 GPU(4090/sm_89)가 개발 GPU(5090/sm_120)보다 지원이 성숙하므로,
> 개발에서 동작하는 휠은 배포에서도 동작한다. GPU 휠은 **paddlepaddle-gpu cu126**
> (4090 네이티브, 드라이버 12.8/12.9 호환) + **torch cu128**(5090·4090 모두 지원)로 고정.

---

## 1. OCR = 3단 파이프라인 (+특수 모듈)

```
입력(이미지/PDF)
   │
 ① Layout Detection  (객체 인식)  ── 본문/제목/표/그림·그래프/각주/머리·바닥글/도장
   │
 ② Text Detection    (글자 위치)  ── 폴리곤 박스            [다운로드 모델, 교체 가능]
   │
 ③ Text Recognition  (글자 인식)  ── 박스 crop → 텍스트     [다운로드 모델, 교체 가능]
   │
 ④ 특수: 표 구조복원(SLANet) · 도장 인식(곡선 텍스트)
   │
 결과 JSON(영역+라인+표) → 시각화 + CER/WER
```

`①②③④`를 각각 **교체 가능한 어댑터**로 구현 → 웹에서 "검출 모델 A + 인식 모델 B"
조합을 골라 비교한다.

---

## 2. 엔진 선택 & 근거

### Primary: PaddleOCR 3.x (PP-StructureV3) — Apache-2.0
요구사항(분리형 det/rec 다운로드 · 한국어 전용 모델 · 레이아웃 · 표 · **도장** ·
CPU/GPU)을 **한 스택에서 전부** 충족하는 유일 후보.

| 단계 | 모델 | 비고 |
|---|---|---|
| Layout | **PP-DocLayout-L** | 23종 영역(각주/머리글/바닥글 구분), mAP 90.4 |
| Detection | **PP-OCRv5 det (DB++)** | server/mobile |
| Recognition | **korean_PP-OCRv5_mobile_rec** / PP-OCRv5_server_rec | 한국어 전용 |
| Orientation | PP-LCNet textline_ori | 회전 보정 |
| Table | SLANet / SLANeXt | 표→HTML |
| Seal | PP-OCRv4 seal det+rec | 빨간 인장 곡선 텍스트 |

### 비교 벤치 (Apache-2.0 기본 3종)
| 엔진 | Detection | Recognition | 레이아웃 | 한국어 | 라이선스 |
|---|---|---|---|---|---|
| **PaddleOCR** ⭐ | DB++ | PP-OCRv5(kor) | PP-DocLayout | ★★★ | Apache-2.0 |
| EasyOCR | CRAFT | CRNN(korean_g2) | 외부 | ★★ | Apache-2.0 |
| Tesseract 5 | LSTM | LSTM(kor) | psm | ★ | Apache-2.0 |

### 옵션 엔진 (기본 off, 법무 검토)
- **DocLayout-YOLO** (실시간 레이아웃) — ⚠️ YOLOv10 기반 AGPL-3.0

---

## 3. 오프라인 설치 전략

**원칙**: 인터넷 되는 빌드 머신에서 전부 받아 → 내부망 반입 → `--no-index` 설치.

1. **Python 패키지**: 버전 핀 고정 → `requirements.lock.txt` → `pip download`로
   **wheelhouse** 생성 → 내부망 `pip install --no-index --find-links=wheelhouse`.
   - paddlepaddle-gpu는 PyPI에 없어 `--index-url paddlepaddle.org.cn/.../cu126` 사용.
   - torch는 `--index-url download.pytorch.org/whl/cu128`.
2. **모델 가중치**: `model_manifest.json`(이름/단계/sha256/로컬경로) →
   `scripts/download_models.py`가 빌드 머신에서 받아 `models/`에 배치 →
   코드는 **로컬 경로로만 로드**. `OCR_OFFLINE=1`이면 HF/PaddleX 자동 다운로드 차단.
3. **시스템 의존성**: tesseract + `kor.traineddata`, poppler(pdf) → `.deb` 오프라인 번들.
4. **검증**: 네트워크 차단 상태 smoke test로 "로컬 로드만으로 동작" 확인.

---

## 4. 테스트 웹

- **백엔드**: FastAPI + uvicorn. `/api/engines`, `/api/run`, `/api/compare`.
- **프런트**: 의존성 없는 vanilla HTML/JS (오프라인 빌드 단계 불필요).
- 좌(컨트롤): 엔진 프리셋 · Layout/Det/Rec 모델 선택 · 이미지·PDF 업로드 · Ground Truth.
- 중앙(시각화): 원본 위 레이아웃 박스(색별) + 텍스트 박스 오버레이.
- 우(결과): 영역별(본문/표/각주/도장) 텍스트 + 신뢰도.
- 하단(지표): CER · WER · 단계별 처리시간 · 멀티엔진 비교 표.

---

## 5. 프로젝트 구조

```
ocr/
├─ backend/
│  ├─ app.py                  # FastAPI
│  ├─ config.py               # 경로/디바이스/오프라인
│  ├─ engines/                # base.py + paddle/hybrid_vl/ollama_vl/easyocr/tesseract 어댑터
│  ├─ pipeline/               # layout→det→rec 조합, 영역 라우팅
│  ├─ layout/                 # 레이아웃 어댑터
│  ├─ metrics/cer_wer.py      # jiwer CER/WER
│  └─ models/registry.py      # 모델 카탈로그/매니페스트
├─ frontend/                  # index.html, app.js, style.css
├─ models/                    # 다운로드 가중치(번들 반입, git 제외)
├─ scripts/                   # download_models.py, make_wheelhouse.sh, smoke_test.py
├─ requirements/              # core/paddle/torch/optional + lock
└─ model_manifest.json
```

---

## 6. 단계별 계획

- **Phase 0** — 스캐폴드 + 오프라인 설치 체계(wheelhouse/manifest). *(진행 중)*
- **Phase 1** — PaddleOCR 풀 파이프라인(layout→det→rec) + 최소 웹, GPU 검증.
- **Phase 2** — 엔진 추상화 + EasyOCR/Tesseract 어댑터, 조합 비교.
- **Phase 3** — 표·도장 모듈, 오버레이 시각화, CER/WER 멀티엔진 비교.
- **Phase 4** — 오프라인 패키징 검증(네트워크 차단) + 내부망 이식 문서.
