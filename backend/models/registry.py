"""모델 카탈로그 로더 — model_manifest.json 을 읽어 웹/엔진에 제공.

웹의 모델 선택 드롭다운, download_models.py 의 다운로드 대상, 엔진의 기본
모델 결정이 모두 이 한 파일을 단일 진실원본(SSOT)으로 참조한다.
"""
from __future__ import annotations

import json
from functools import lru_cache

from backend.config import PROJECT_ROOT

MANIFEST_PATH = PROJECT_ROOT / "model_manifest.json"


@lru_cache(maxsize=1)
def load_manifest() -> dict:
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def list_engines() -> list[dict]:
    """웹 표시용 엔진 목록 (id, label, license, 기본활성)."""
    m = load_manifest()
    out = []
    for eid, e in m["engines"].items():
        out.append({
            "id": eid,
            "label": e.get("label", eid),
            "license": e.get("license", ""),
            "enabled_by_default": e.get("enabled_by_default", True),
            "stages": {stage: opts for stage, opts in e.get("stages", {}).items()},
        })
    return out


def stage_models(engine: str, stage: str) -> list[dict]:
    m = load_manifest()
    return m["engines"].get(engine, {}).get("stages", {}).get(stage, [])


def default_model(engine: str, stage: str) -> str | None:
    for opt in stage_models(engine, stage):
        if opt.get("default"):
            return opt["id"]
    opts = stage_models(engine, stage)
    return opts[0]["id"] if opts else None
