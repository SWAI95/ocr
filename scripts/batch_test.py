"""문서 일괄 테스트 — 폴더 안의 이미지/PDF 를 엔진으로 OCR 하고 요약.

사용 예:
  # 문서는 레이아웃 ON(PPStructureV3)이 정석 — 권장
  python scripts/batch_test.py samples/dataset/korean_docs --engine paddle --table
  # 멀티엔진 비교
  python scripts/batch_test.py <폴더> --engine paddle --table
  python scripts/batch_test.py <폴더> --engine ollama_vl --rec qwen2.5vl:7b

정답(CER): 같은 이름의 `<파일이름>.gt.txt` 가 있으면 CER/WER 자동 계산.
결과 텍스트는 data/outputs/ 에 저장된다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.engines.base import EngineOptions  # noqa: E402
from backend.imaging import decode_image  # noqa: E402
from backend.metrics.cer_wer import score  # noqa: E402
from backend.pipeline import runner  # noqa: E402

_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".pdf"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="문서 폴더 또는 단일 파일")
    ap.add_argument("--engine", default="hybrid_vl")
    ap.add_argument("--rec", default=None, help="recognition 모델(예: qwen2.5vl:7b)")
    ap.add_argument("--table", action="store_true", help="표/레이아웃 인식(PPStructureV3)")
    ap.add_argument("--layout", action="store_true", help="레이아웃 검출만")
    ap.add_argument("--no-gpu", action="store_true")
    ap.add_argument("--preprocess", default="",
                    help="전처리 스텝(쉼표): red_stamp,flatten,barcode,denoise")
    ap.add_argument("--max-pages", type=int, default=3)
    args = ap.parse_args()
    pp_steps = [s.strip() for s in args.preprocess.split(",") if s.strip()]

    root = Path(args.path)
    files = sorted([p for p in ([root] if root.is_file() else root.rglob("*"))
                    if p.suffix.lower() in _EXTS])
    if not files:
        print(f"[!] {root} 에 이미지/PDF 없음"); return

    if not runner.is_available(args.engine):
        print(f"[!] 엔진 '{args.engine}' 미설치/비활성"); return

    print(f"[*] 엔진={args.engine} table={args.table} layout={args.layout} "
          f"rec={args.rec or '기본'} | 파일 {len(files)}개\n")
    rows = []
    for f in files:
        try:
            pages = decode_image(f.read_bytes(), f.name)
        except Exception as e:  # noqa: BLE001
            print(f"  [!] {f.name}: 디코드 실패 {e}"); continue
        # 단일 GT (page 0 폴백): <파일>.gt.txt
        base_gt = None
        for cand in (f.with_suffix(f.suffix + ".gt.txt"), f.with_suffix(".gt.txt")):
            if cand.exists():
                base_gt = cand.read_text(); break

        for pi, img in enumerate(pages[:args.max_pages]):
            opts = EngineOptions(lang="korean", use_gpu=not args.no_gpu,
                                 use_layout=args.layout or args.table,
                                 use_table=args.table, rec_model=args.rec)
            if pp_steps:
                from backend.preprocess import preprocess as _pp
                img = _pp(img, pp_steps)[0]
            t0 = time.perf_counter()
            try:
                r = runner.run(args.engine, img, opts)
            except Exception as e:  # noqa: BLE001
                print(f"  [!] {f.name} p{pi}: 실행 오류 {str(e)[:80]}"); continue
            wall = time.perf_counter() - t0
            tag = f"{f.stem}_p{pi}"
            (config.OUTPUT_DIR / f"{tag}.{args.engine}.txt").write_text(r.full_text)
            # 페이지별 GT 우선: <파일stem>_p{pi}.gt.txt, 없으면 page0만 단일 GT
            page_gt = f.with_name(f"{f.stem}_p{pi}.gt.txt")
            gt = page_gt.read_text() if page_gt.exists() else (base_gt if pi == 0 else None)
            cer = score(gt, r.full_text).cer if gt else None
            rows.append((tag, len(r.lines), len(r.tables), wall, cer))
            cer_s = f"{cer*100:5.1f}%" if cer is not None else "   -  "
            print(f"  {tag:28s} 라인 {len(r.lines):3d}  표 {len(r.tables)}  "
                  f"CER {cer_s}  {wall:5.1f}s")

    print(f"\n[OK] 결과 텍스트: {config.OUTPUT_DIR}")
    cers = [c for *_, c in rows if c is not None]
    if cers:
        print(f"[=] 평균 CER: {sum(cers)/len(cers)*100:.1f}%  (GT 있는 {len(cers)}건)")


if __name__ == "__main__":
    main()
