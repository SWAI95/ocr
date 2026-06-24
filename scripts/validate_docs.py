"""문서 단위(micro) 검증 — doc_00/04/05 를 엔진별로 OCR 해 문서 micro CER/WER 산출.

보고서(make_report.py)용 실측치. 각 문서의 모든 페이지를 이어붙여(full_text) 페이지
GT 를 이어붙인 정답과 한 번에 채점한다(웹 /api/run 과 동일 micro 방식).

사용: ./.venv/bin/python scripts/validate_docs.py paddle paddle_vl hybrid_vl
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.engines.base import EngineOptions  # noqa: E402
from backend.imaging import decode_image  # noqa: E402
from backend.metrics.cer_wer import score  # noqa: E402
from backend.pipeline import runner  # noqa: E402

GTD = Path("samples/dataset/korean_docs")
DOCS = {"doc_00": "doc_00.pdf", "doc_04": "doc_04.pdf", "doc_05": "doc_05.pdf"}


def doc_gt(stem: str, npages: int) -> str:
    parts = []
    for i in range(npages):
        p = GTD / f"{stem}_p{i}.gt.txt"
        if p.exists():
            parts.append(p.read_text())
    return "\n".join(parts)


def main() -> None:
    engines = sys.argv[1:] or ["paddle", "paddle_vl", "hybrid_vl"]
    print(f"엔진: {engines}\n")
    results: dict[str, dict[str, tuple]] = {e: {} for e in engines}
    for eng in engines:
        if not runner.is_available(eng):
            print(f"[!] {eng} 비가용"); continue
        for stem, fname in DOCS.items():
            pages = decode_image((GTD / fname).read_bytes(), fname)
            opts = EngineOptions(lang="korean", use_gpu=True,
                                 use_layout=True, use_table=True)
            t0 = time.perf_counter()
            texts = []
            for img in pages:
                texts.append(runner.run(eng, img, opts).full_text)
            wall = time.perf_counter() - t0
            full = "\n".join(texts)
            gt = doc_gt(stem, len(pages))
            sc = score(gt, full)
            results[eng][stem] = (sc.cer * 100, sc.wer * 100, wall, len(pages))
            print(f"  {eng:11s} {stem}  CER {sc.cer*100:5.2f}%  "
                  f"WER {sc.wer*100:6.2f}%  {wall:6.1f}s ({len(pages)}p)")
        # 문서 평균(매크로)
        cers = [v[0] for v in results[eng].values()]
        if cers:
            print(f"  {eng:11s} ----  평균 CER {sum(cers)/len(cers):5.2f}%\n")

    # 보고서용 요약 표 (CER / WER)
    print("\n=== 보고서용 (엔진 | doc_00 | doc_04 | doc_05 | 평균CER) ===")
    for eng in engines:
        r = results.get(eng, {})
        if not r:
            continue
        cells = []
        for stem in DOCS:
            if stem in r:
                cells.append(f"{r[stem][0]:.2f} / {r[stem][1]:.1f}")
            else:
                cells.append("—")
        avg = sum(v[0] for v in r.values()) / len(r)
        print(f"{eng:12s} | " + " | ".join(cells) + f" | {avg:.2f}%")


if __name__ == "__main__":
    main()
