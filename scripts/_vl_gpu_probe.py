"""임시: PaddleOCR-VL 가 5090(Blackwell) GPU 에서 실제 텍스트를 내는지 확인.
사용: ./.venv/bin/python scripts/_vl_gpu_probe.py [gpu:0|cpu] [이미지경로]
"""
import os
import re
import sys
import time

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
import cv2  # noqa: E402
from paddleocr import PaddleOCRVL  # noqa: E402

_MD_STRIP = re.compile(r"[#*`>|]|<[^>]+>")


def md_to_text(md: str) -> str:
    out = []
    for ln in str(md).splitlines():
        t = _MD_STRIP.sub(" ", ln).strip()
        if t:
            out.append(re.sub(r"\s{2,}", " ", t))
    return "\n".join(out)


dev = sys.argv[1] if len(sys.argv) > 1 else "gpu:0"
path = sys.argv[2] if len(sys.argv) > 2 else "samples/dataset/korean_docs/doc_02.jpg"
img = cv2.imread(path)
pipe = PaddleOCRVL(vl_rec_backend="native", device=dev,
                   use_doc_orientation_classify=False, use_doc_unwarping=False)
t = time.perf_counter()
out = pipe.predict(img)
dt = time.perf_counter() - t
res = out[0]
# paddleocr 3.6.0: markdown 은 @property (res.markdown), 키는 markdown_texts.
md = None
for accessor in (lambda: res.markdown, lambda: res["markdown"]):
    try:
        md = accessor()
        if md:
            break
    except Exception:
        continue
md_txt = ""
if isinstance(md, dict):
    md_txt = md.get("markdown_texts") or md.get("text") or ""
elif md is not None:
    md_txt = str(md)
txt = md_to_text(md_txt)
print(f"### DEV={dev} TIME={dt:.1f}s md_type={type(md).__name__} text_len={len(txt)}")
print("### SAMPLE:")
print(txt[:500])
