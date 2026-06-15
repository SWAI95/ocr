# 오프라인 한국어 문서 OCR 엔진 + 테스트 벤치

한글+영어+숫자+기호 문서(계약서·표·그래프·각주·도장 포함)를 **객체 인식 →
글자 인식** 2단계로 처리하는 **완전 오프라인** OCR 엔진과, 여러 엔진/모델을
바꿔가며 CER/WER 을 비교하는 테스트 웹.

> 설계 배경과 모델 선택 근거는 [DESIGN.md](DESIGN.md) 참고.

## 엔진

| 엔진 | 역할 | Detection | Recognition | Layout | 라이선스 |
|---|---|---|---|---|---|
| **hybrid_vl** ⭐ | 기본(문서) | Paddle layout | 표·폼=Paddle · 줄글=Qwen2.5VL | Paddle | Apache-2.0 + model별 |
| **PaddleOCR 3.x** | Primary | PP-OCRv5 det | PP-OCRv5(kor) | PP-DocLayout | Apache-2.0 |
| ollama_vl | VLM(로컬) | — | Qwen2.5VL(Ollama) | — | model별 |
| paddle_vl | 4090 VLM | VLM | PaddleOCR-VL-0.9B | VLM | Apache-2.0 |
| EasyOCR | 비교 | CRAFT | CRNN(korean_g2) | — | Apache-2.0 |
| Tesseract 5 | baseline | LSTM | LSTM(kor) | — | Apache-2.0 |

## 환경

- Ubuntu 24.04 / Python 3.12 / GPU(권장)
- 개발: RTX 5090(CUDA 12.8) · 배포: RTX 4090(CUDA 12.9)
- GPU 휠 고정: **paddlepaddle-gpu cu126** + **torch cu128** (두 GPU 모두 호환)

---

## A. 개발 머신 설치 (인터넷 O)

```bash
python3.12 -m venv .venv
. .venv/bin/activate

# 1) core
pip install -r requirements/core.txt

# 2) PaddleOCR (GPU, 공식 인덱스)
pip install -r requirements/paddle.txt \
  --index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/ \
  --extra-index-url https://pypi.org/simple

# 3) EasyOCR + torch (cu128)
pip install -r requirements/torch.txt \
  --index-url https://download.pytorch.org/whl/cu128 \
  --extra-index-url https://pypi.org/simple

# 4) (옵션) DocLayout-YOLO — 라이선스(AGPL) 검토 후
# pip install -r requirements/optional.txt
```

시스템 의존성(Tesseract·PDF·한글폰트, **sudo 필요**):
```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-kor poppler-utils fonts-nanum
```

모델 다운로드(빌드 머신에서 1회):
```bash
python scripts/download_models.py --all          # 기본 모델
# python scripts/download_models.py --all-variants # 매니페스트 전체
```

실행:
```bash
uvicorn backend.app:app --host 0.0.0.0 --port 8000
# 브라우저: http://localhost:8000
```

---

## B. 내부망(오프라인) 이관  — 배포 타깃: RTX 4090 (cu126 통일)

> dev(5090)는 torch cu128 이지만, **배포 4090에서는 torch 도 cu126** 으로 맞춰
> paddle(cu126)과 CUDA 라이브러리를 공유한다(둘 다 GPU). 배포 의존성은
> `requirements/deploy.txt`. 검증된 흐름:

**1) 빌드 머신(인터넷 O)에서 번들 생성**
```bash
# (a) 모델 수집 → models/  (PaddleX 캐시 홈 방식, ~1.8GB)
python scripts/download_models.py            # 텍스트+레이아웃+표+도장
#   --with-formula  : 수식/차트 모델까지(+크기)

# (b) 파이썬 휠 수집 → wheelhouse/  (torch·paddle 모두 cu126)
bash scripts/make_wheelhouse.sh              # REQ=requirements/deploy.txt 기본

# (c) 시스템 패키지(.deb): tesseract/poppler/한글폰트
mkdir -p debs && cd debs
apt-get download tesseract-ocr tesseract-ocr-kor libtesseract5 poppler-utils fonts-nanum
cd ..
```

**2) 내부망으로 반입:** `wheelhouse/`, `requirements/deploy.txt`, `models/`,
`model_manifest.json`, `debs/`, 코드 전체.

**3) 내부망(인터넷 X)에서 설치**
```bash
sudo dpkg -i debs/*.deb || sudo apt-get -f install   # tesseract/poppler/폰트
bash scripts/install_offline.sh                       # venv + --no-index 설치
```

**4) 오프라인 검증 & 실행** (`OCR_OFFLINE=1` → 외부 다운로드 차단, 번들 모델만 로드)
```bash
OCR_OFFLINE=1 python scripts/smoke_test.py            # 로컬 모델 로드 확인
OCR_OFFLINE=1 uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

`OCR_OFFLINE=1` 이면: `HF_HUB_OFFLINE=1`, `PADDLE_PDX_CACHE_HOME=models/paddle`,
`PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True` 가 자동 설정되어 **네트워크 없이
`models/` 의 번들 가중치만으로 동작**한다. (개발 머신에서 프록시 차단 + 네트워크
격리로 검증 완료.)

> 4090에서 paddle GPU가 정상인지 최초 1회 확인: `python -c "import paddle,paddle.nn as nn;
> paddle.set_device('gpu:0'); print(float(nn.Conv2D(3,8,3)(paddle.randn([1,3,32,32])).abs().sum()))"`
> → 0 이 아니면 정상(Blackwell 의 0-출력 버그 없음).

---

## 프로젝트 구조

```
backend/   app.py · config.py · engines/ · pipeline/ · metrics/ · models/
frontend/  index.html · app.js · style.css
scripts/   download_models.py · make_wheelhouse.sh · install_offline.sh · smoke_test.py
requirements/  core · paddle · torch · optional · deploy.txt(배포 cu126 통일)
models/    paddle/official_models(번들 ~1.8GB) · easyocr · hf · tessdata (git 제외)
model_manifest.json   선택 가능한 모델 카탈로그(SSOT)
```

## 테스트 방법 (문서 PDF)

문서는 **레이아웃 ON(PPStructureV3)** 이 정석 — 실문서(고용노동부 면접확인서)에서
영역을 잘라 인식하면 CER 1.4%(레이아웃 OFF 41.6% → ON 1.4%). PDF 는 poppler
불필요(pypdfium2 내장, 다중 페이지 OK).

**1) 웹 (대화형) — http://localhost:8000**
1. 문서 PDF/이미지 업로드 (PDF 다중 페이지는 '페이지' 번호로 이동)
2. 엔진 `paddle` + **"표 구조 인식" 체크** ← 문서의 핵심 설정
3. 정답을 알면 Ground Truth 칸에 붙여넣어 CER/WER 확인
4. "멀티엔진 비교" 로 paddle / easyocr / ollama_vl 나란히 비교

**2) 일괄 (폴더) — scripts/batch_test.py**
```bash
python scripts/batch_test.py <폴더> --engine paddle --table   # 문서=레이아웃 ON
# 정답: 같은 이름 <파일>.gt.txt 있으면 CER 자동, 결과 텍스트 → data/outputs/
python scripts/batch_test.py <폴더> --engine ollama_vl --rec qwen2.5vl:7b  # VLM 비교
```

**권장/주의**
- 문서 정식 설정: `paddle --table`(레이아웃 ON). 그 외엔 reading order 가 깨질 수 있음.
- dev 빠른 반복: `easyocr`(GPU ~2s) 또는 `ollama_vl`(GPU). paddle 은 dev 5090 에서
  CPU(~80s/page, Blackwell 제약), 배포 4090 GPU 에선 ~3-5s.
- 실측은 본인 실제 계약서 PDF 로: `samples/dataset/` 에 넣고 위 명령 실행.

## API

- `GET /api/engines` — 엔진/모델 카탈로그 + 설치 가용성
- `POST /api/run` — 이미지/PDF 업로드 → OCR 결과(영역/라인/표/오버레이/CER·WER)
- `POST /api/compare` — 같은 입력을 여러 엔진으로 비교
