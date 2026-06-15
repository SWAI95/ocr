"""Ollama VLM 어댑터 — 로컬 Ollama 서버(기본 127.0.0.1:11434)로 비전 모델 OCR.

dev RTX5090(Blackwell)에서도 동작: Ollama 의 자체 llama.cpp/CUDA 빌드가 최신
GPU 를 지원하므로 paddle 의 sm_120 문제를 우회한다. 완전 로컬(외부 API 아님).
stdlib urllib 만 사용 — 새 파이썬 의존성 없음.

환경변수:
  OLLAMA_HOST   기본 "127.0.0.1:11434"
  OLLAMA_MODEL  기본 "qwen2.5vl:7b"  (없으면 web 에서 모델 선택)
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request

import cv2
import numpy as np

from backend.engines.base import BBox, EngineOptions, OCREngine, OCRResult, TableResult, TextLine

_DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434").replace("http://", "")
_DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5vl:7b")

_OCR_PROMPT = (
    "You are a precise OCR engine for Korean business documents (contracts, forms). "
    "Transcribe ALL text in this image EXACTLY as it appears, in natural top-to-bottom "
    "reading order, preserving line breaks.\n"
    "Rules:\n"
    "1) Output Korean in Hangul. NEVER substitute Chinese/Hanja characters for Korean "
    "syllables (write 의, not 的). Use Hanja ONLY if Hanja actually appears in the image.\n"
    "2) Transcribe each line exactly once. Do NOT repeat, merge, or carry text from one "
    "line into another line.\n"
    "3) Do NOT invent, complete, summarize, or add any text not visibly present. If text "
    "is unclear, transcribe only what is visible — never guess a plausible ending.\n"
    "4) Transcribe headers, footers, stamps and logos in the position where they appear "
    "(company logos/footers belong at the bottom, not inside body paragraphs).\n"
    "5) Keep numbers, dates, the won sign ₩, parentheses, punctuation and symbols exactly "
    "as shown. Do NOT translate.\n"
    "6) For tables, output GitHub-flavored markdown tables. Do NOT add markdown headings, "
    "bullet points, or list markers that are not in the image.\n"
    "Output ONLY the transcription."
)

# 표 구분선/수평선(---, --- ---, |--|--|, ===) 한 줄 통째 제거용
_MD_SEP = re.compile(r"^[\s\-:|=_]+$")
# 선행 불릿("- ", "* ", "+ ") — Qwen 이 본문에 덧붙이는 마크다운 불릿 제거
_MD_BULLET = re.compile(r"^\s*[-*+]\s+")
# 인라인 마크다운 기호
_MD_INLINE = re.compile(r"[`*#>]")


def _api(host: str, path: str, payload: dict | None = None, timeout: float = 300):
    url = f"http://{host}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _list_models(host: str) -> list[str]:
    try:
        return [m["name"] for m in _api(host, "/api/tags", timeout=5).get("models", [])]
    except Exception:  # noqa: BLE001
        return []


def _md_table_to_text(md: str) -> str:
    out = []
    for ln in md.splitlines():
        s = ln.strip()
        if not s or _MD_SEP.match(s):
            continue                       # 빈 줄 / 표구분선·수평선 제거
        s = _MD_BULLET.sub("", s)          # 선행 불릿 제거
        t = s.strip("|").replace("|", " ")  # 표 파이프 → 공백
        t = _MD_INLINE.sub("", t)          # 인라인 기호 제거
        t = re.sub(r"\s{2,}", " ", t).strip()
        if t:
            out.append(t)
    return "\n".join(out)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_MD_SEP_CELL = re.compile(r"^:?-{2,}:?$")  # 마크다운 표 구분행 셀(---, :--:, ---:)


def _md_table_to_html(md: str) -> str:
    """VLM 이 낸 markdown 표(| a | b |)를 신뢰 가능한 <table> HTML 로 변환.

    셀 내용은 모델 출력(비신뢰)이라 반드시 이스케이프해 주입한다(태그는 자체 생성).
    구분행(| --- | --- |)은 건너뛰고, 첫 행은 프론트 CSS 가 헤더로 스타일링한다.
    """
    rows = []
    for ln in md.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and all(_MD_SEP_CELL.match(c) for c in cells):
            continue  # 헤더 구분행 스킵
        rows.append(cells)
    if not rows:
        return ""
    parts = ["<table>"]
    for r in rows:
        parts.append("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>")
    parts.append("</table>")
    return "".join(parts)


class OllamaVLEngine(OCREngine):
    name = "ollama_vl"
    capabilities = {"recognition", "table"}

    def __init__(self) -> None:
        self.host = _DEFAULT_HOST

    # 모델 선호 순위(설치된 것 중 첫 번째 자동 선택)
    _PREFERENCE = ["qwen2.5vl:7b", "qwen2.5vl:3b", "gemma3:27b"]

    def _resolve_model(self, opts: EngineOptions, avail: list[str]) -> str:
        def installed(m: str) -> bool:
            return m in avail or f"{m}:latest" in avail
        if opts.rec_model:
            if not installed(opts.rec_model):
                raise RuntimeError(
                    f"모델 '{opts.rec_model}' 미설치. 설치된 모델: {avail}. "
                    f"'ollama pull {opts.rec_model}' 로 받으세요.")
            return opts.rec_model
        # 기본/선호 → 없으면 설치된 비전 모델 중 하나
        for m in [_DEFAULT_MODEL, *self._PREFERENCE]:
            if installed(m):
                return m
        # 마지막 폴백: 설치된 첫 모델(텍스트 전용일 수 있으나 사용자 선택 존중)
        if avail:
            return avail[0]
        raise RuntimeError("Ollama 에 설치된 모델이 없습니다. 'ollama pull qwen2.5vl:7b'.")

    def run(self, image: np.ndarray, opts: EngineOptions) -> OCRResult:
        avail = _list_models(self.host)
        if not avail:
            raise RuntimeError(
                f"Ollama 서버에 접근 불가 (http://{self.host}). 'ollama serve' 가 "
                "0.0.0.0 로 떠 있는지 확인하세요. (WSL→Windows 는 OLLAMA_HOST=0.0.0.0)")
        model = self._resolve_model(opts, avail)

        ok, buf = cv2.imencode(".jpg", image)
        b64 = base64.b64encode(buf.tobytes()).decode()

        t0 = time.perf_counter()
        # temperature=0(greedy) + 고정 seed → Qwen 출력 재현 가능(웹↔보고서 일치).
        # seed 없으면 실행마다 미세 변동 → hybrid 안전장치 병합 on/off 가 흔들려 CER 진폭 큼.
        resp = _api(self.host, "/api/generate", {
            "model": model,
            "prompt": _OCR_PROMPT,
            "images": [b64],
            "stream": False,
            "options": {"temperature": 0, "num_predict": 4096,
                        "seed": int(os.environ.get("OCR_OLLAMA_SEED", "0"))},
        })
        dt = round((time.perf_counter() - t0) * 1000, 1)
        text = (resp.get("response") or "").strip()

        # 표(markdown) 추출 → 신뢰 가능한 <table> HTML 로 변환.
        # 연속된 '|' 시작 줄을 한 표 블록으로 묶는다(마지막 줄에 개행 없어도 누락 안 됨).
        tables: list[TableResult] = []
        block: list[str] = []
        for ln in text.splitlines() + [""]:   # 끝 sentinel 로 마지막 블록 flush
            if ln.strip().startswith("|"):
                block.append(ln)
                continue
            if len(block) >= 2:               # 헤더+구분행 이상일 때만 표로 인정
                html = _md_table_to_html("\n".join(block))
                if html:
                    tables.append(TableResult(bbox=BBox(0, 0, 0, 0), html=html))
            block = []

        # 라인: markdown 정리 후 줄 단위 (VLM 이라 박스 없음)
        lines: list[TextLine] = []
        for ln in _md_table_to_text(text).splitlines():
            if ln.strip():
                lines.append(TextLine(polygon=[[0, 0]] * 4, text=ln.strip(), score=1.0))

        return OCRResult(
            engine=self.name, lines=lines, regions=[], tables=tables,
            timings_ms={"vlm_ms": dt}, device=f"ollama:{model}",
            meta={"model": model, "host": self.host,
                  "eval_count": resp.get("eval_count")})
