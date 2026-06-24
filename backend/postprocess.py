"""OCR 후처리 — (1) 결정론적 글자 정규화(항상, 무비용)  (2) 로컬 LLM 교정(옵션).

(1) normalize_ocr_text: 인식기가 구조적으로 못 가르는 글리프를 규칙으로 교정한다.
    대표 사례 = 원화기호 ₩(가로줄 그은 W)를 라틴 'W' 로 읽는 문제. 한국어 계약서에서
    '숫자 앞 W' 는 사실상 항상 원화이므로 결정론적으로 ₩ 로 치환한다(런타임 비용 0,
    비결정성 0). runner.run 이 전 엔진 출력에 일괄 적용한다. 끄려면 OCR_NORMALIZE=off.

(2) llm_correct: 로컬 Ollama LLM 으로 문맥 오타 교정(옵션, 기본 미사용).
    ⚠️ 법적 문서 환각 위험: '내용 변경 금지' 프롬프트. 교정 on/off CER 측정 후 채택.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

_HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434").replace("http://", "")
_DEFAULT_MODEL = os.environ.get("OCR_CORRECT_MODEL", "gpt-oss:20b")

# 원화기호 교정: 앞에 영문/한글이 붙지 않은 단독 W/Ｗ 가 (공백 0~1개 뒤) 숫자를
# 이끌면 ₩ 로 치환. 음의 룩비하인드로 'KRW 100', 'VW3' 같은 통화코드/모델명은 보존.
# (CER 메트릭은 ₩→\\ 동치 처리 → GT 의 '\\100,000' 과도 일치.) 사용자 지정 규칙.
_WON_RE = re.compile(r"(?<![A-Za-z가-힣])[WＷ](?=\s?\d)")


# VLM(paddle_vl/ollama_vl)이 가끔 줄바꿈을 리터럴 '\n'(역슬래시+n) 2글자로 출력하는
# 아티팩트 — 공백으로 접어 군더더기 글자 제거(paddle 출력엔 없어 무영향).
_LITERAL_ESC_RE = re.compile(r"\\[ntr]")


def normalize_ocr_text(text: str) -> str:
    """결정론적 글자 정규화. 비활성: 환경변수 OCR_NORMALIZE=off."""
    if not text or os.environ.get("OCR_NORMALIZE", "on").lower() in ("0", "off", "false"):
        return text
    text = _LITERAL_ESC_RE.sub(" ", text)
    return _WON_RE.sub("₩", text)

_PROMPT = (
    "너는 한국어 문서 OCR 교정기다. 아래는 OCR 결과 텍스트다. "
    "OCR 이 잘못 인식한 '글자 오타'만 문맥에 맞게 고쳐라. "
    "규칙: (1) 내용을 추가/삭제/요약/번역하지 마라. "
    "(2) 줄 구성과 순서를 그대로 유지하라. "
    "(3) 숫자, 날짜, 금액, 회사명·사람이름 같은 고유명사는 임의로 바꾸지 마라. "
    "(4) 설명 없이 교정된 텍스트만 출력하라.\n\n[OCR]\n"
)

# gpt-oss 등 reasoning 모델의 <think>...</think> 흔적 제거
_THINK = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)


def llm_correct(text: str, model: str | None = None, host: str | None = None,
                timeout: float = 180) -> str:
    """Ollama LLM 으로 OCR 텍스트 교정. 실패 시 원문 그대로 반환."""
    if not text.strip():
        return text
    model = model or _DEFAULT_MODEL
    host = host or _HOST
    payload = {
        "model": model,
        "prompt": _PROMPT + text,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 4096},
    }
    try:
        req = urllib.request.Request(
            f"http://{host}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode())
        out = _THINK.sub("", resp.get("response", "") or "").strip()
        # 코드펜스 제거
        out = re.sub(r"^```[a-z]*\n?|\n?```$", "", out).strip()
        return out or text
    except Exception:  # noqa: BLE001
        return text
