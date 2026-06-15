"""OCR 후처리 교정 — 로컬 Ollama LLM 으로 문자 오타 교정 (정확도 우선 정책).

⚠️ 법적 문서 환각 위험: '내용 변경 금지' 프롬프트 + 원문 diff 제공. 교정 on/off
CER 을 측정해 실제 도움 될 때만 채택한다. 완전 로컬(외부 API 아님).
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

_HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434").replace("http://", "")
_DEFAULT_MODEL = os.environ.get("OCR_CORRECT_MODEL", "gpt-oss:20b")

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
