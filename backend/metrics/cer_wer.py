"""인식률 지표 — CER / WER (jiwer 기반).

한글 문서는 띄어쓰기가 불안정하므로 CER 을 기본 지표로 본다. WER 은 공백
토큰 기준이라 한글에선 참고용. normalize 옵션으로 공백/대소문자/구두점
정규화를 켜고 끌 수 있다.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict

import jiwer


@dataclass
class ScoreResult:
    cer: float                 # 0~1 (낮을수록 좋음)
    wer: float
    cer_accuracy: float        # 1 - cer (클램프 0~1)
    wer_accuracy: float
    ref_chars: int
    hyp_chars: int
    char_substitutions: int
    char_deletions: int
    char_insertions: int
    char_hits: int

    def to_dict(self) -> dict:
        return asdict(self)


# 타이포그래피 동치 — 곡선/곧은 따옴표, 대시, 원화기호는 같은 글자로 취급.
# (OCR이 곡선따옴표 “ ”를 맞게 읽어도 GT가 곧은따옴표 "면 오류로 세는 문제 방지.
#  인식 정확도를 재는 것이지 글꼴/표기 차이를 재는 게 아니므로 정당함.)
_TYPO_MAP = {
    "“": '"', "”": '"', "„": '"', "‟": '"', "＂": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "＇": "'",
    "–": "-", "—": "-", "―": "-", "−": "-",
    "₩": "\\", "￦": "\\",  # 원화기호 ₩ → \\ (한국 코드페이지 동치)
}
_TYPO_RE = re.compile("|".join(map(re.escape, _TYPO_MAP)))


def _normalize(text: str, *, strip_space: bool, lower: bool,
               drop_punct: bool, nfkc: bool, unify_typo: bool = True) -> str:
    if nfkc:
        text = unicodedata.normalize("NFKC", text)
    if unify_typo:
        text = _TYPO_RE.sub(lambda m: _TYPO_MAP[m.group()], text)
    if lower:
        text = text.lower()
    if drop_punct:
        # 한글/영문/숫자/공백만 남김
        text = re.sub(r"[^\w\s가-힣]", "", text, flags=re.UNICODE)
    if strip_space:
        text = re.sub(r"\s+", "", text)
    else:
        text = re.sub(r"\s+", " ", text).strip()
    return text


def score(reference: str, hypothesis: str, *,
          strip_space_for_cer: bool = True,
          lower: bool = True,
          drop_punct: bool = False,
          nfkc: bool = True) -> ScoreResult:
    """ground truth(reference) 대비 OCR 결과(hypothesis) 평가."""
    # CER 용 정규화 (공백 제거가 기본)
    ref_c = _normalize(reference, strip_space=strip_space_for_cer, lower=lower,
                       drop_punct=drop_punct, nfkc=nfkc)
    hyp_c = _normalize(hypothesis, strip_space=strip_space_for_cer, lower=lower,
                       drop_punct=drop_punct, nfkc=nfkc)
    # WER 용 정규화 (공백 유지 — 토큰 분리 필요)
    ref_w = _normalize(reference, strip_space=False, lower=lower,
                       drop_punct=drop_punct, nfkc=nfkc)
    hyp_w = _normalize(hypothesis, strip_space=False, lower=lower,
                       drop_punct=drop_punct, nfkc=nfkc)

    if not ref_c:
        # 레퍼런스가 비면 지표 정의 불가 — 빈 결과
        return ScoreResult(0.0 if not hyp_c else 1.0, 0.0 if not hyp_w else 1.0,
                           1.0 if not hyp_c else 0.0, 1.0 if not hyp_w else 0.0,
                           0, len(hyp_c), 0, 0, len(hyp_c), 0)

    cer_out = jiwer.process_characters(ref_c, hyp_c)
    cer_val = cer_out.cer if hasattr(cer_out, "cer") else jiwer.cer(ref_c, hyp_c)
    wer_val = jiwer.wer(ref_w, hyp_w) if ref_w else (0.0 if not hyp_w else 1.0)

    return ScoreResult(
        cer=round(float(cer_val), 4),
        wer=round(float(wer_val), 4),
        cer_accuracy=round(max(0.0, 1.0 - float(cer_val)), 4),
        wer_accuracy=round(max(0.0, 1.0 - float(wer_val)), 4),
        ref_chars=len(ref_c),
        hyp_chars=len(hyp_c),
        char_substitutions=int(getattr(cer_out, "substitutions", 0)),
        char_deletions=int(getattr(cer_out, "deletions", 0)),
        char_insertions=int(getattr(cer_out, "insertions", 0)),
        char_hits=int(getattr(cer_out, "hits", 0)),
    )
