"""엔진 추상화 — Detection / Recognition / Layout 을 교체 가능한 부품으로.

OCR 파이프라인은 세 단계로 분해된다:
    ① LayoutDetector   : 이미지 → 영역(본문/표/그림/각주/도장 ...)  [객체 인식]
    ② TextDetector     : 이미지 → 글자 박스(폴리곤)                  [Detection]
    ③ TextRecognizer   : 박스별 crop → 텍스트                        [Recognition]

각 엔진은 위 중 일부 또는 전부를 구현한다. 웹 벤치는 이 인터페이스 단위로
모델을 골라 조합하고 CER/WER 로 비교한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# 레이아웃 영역 표준 라벨 (엔진별 라벨을 여기로 정규화)
LABEL_TEXT = "text"
LABEL_TITLE = "title"
LABEL_TABLE = "table"
LABEL_FIGURE = "figure"      # 그림/그래프/차트
LABEL_FOOTNOTE = "footnote"
LABEL_HEADER = "header"
LABEL_FOOTER = "footer"
LABEL_SEAL = "seal"          # 도장/인장
LABEL_FORMULA = "formula"
LABEL_OTHER = "other"


@dataclass
class BBox:
    """축 정렬 사각형 (픽셀 좌표)."""
    x1: float
    y1: float
    x2: float
    y2: float

    def as_list(self) -> list[float]:
        return [self.x1, self.y1, self.x2, self.y2]

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)


@dataclass
class LayoutRegion:
    """① 레이아웃 검출 결과(객체 인식)."""
    bbox: BBox
    label: str          # 위 LABEL_* 중 하나
    score: float = 1.0
    raw_label: str = ""  # 엔진 고유 라벨(디버그용)


@dataclass
class TextBox:
    """② 텍스트 검출 결과 — 4점 폴리곤(회전/기울기 대응)."""
    polygon: list[list[float]]   # [[x,y] x4]
    score: float = 1.0

    def bbox(self) -> BBox:
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        return BBox(min(xs), min(ys), max(xs), max(ys))


@dataclass
class TextLine:
    """③ 최종 인식 결과 한 줄."""
    polygon: list[list[float]]
    text: str
    score: float = 1.0
    region_label: Optional[str] = None  # 소속 레이아웃 영역


@dataclass
class TableResult:
    """표 구조 복원 결과."""
    bbox: BBox
    html: str = ""               # <table>...</table>
    cells: list[dict] = field(default_factory=list)


@dataclass
class OCRResult:
    """엔진 1회 실행의 표준 출력."""
    engine: str
    lines: list[TextLine] = field(default_factory=list)
    regions: list[LayoutRegion] = field(default_factory=list)
    tables: list[TableResult] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    device: str = "cpu"
    meta: dict = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n".join(l.text for l in self.lines if l.text)


@dataclass
class EngineOptions:
    """실행 옵션 — 웹에서 전달되는 모델 선택 파라미터."""
    lang: str = "korean"          # korean / en / ...
    use_gpu: bool = True
    use_layout: bool = True       # 레이아웃(객체 인식) 단계 사용 여부
    use_table: bool = False       # 표 구조 복원
    use_seal: bool = False        # 도장 인식
    det_model: Optional[str] = None
    rec_model: Optional[str] = None
    layout_model: Optional[str] = None
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# 부품 인터페이스 (엔진은 해당하는 것만 구현)
# --------------------------------------------------------------------------
class LayoutDetector(ABC):
    @abstractmethod
    def detect_layout(self, image: np.ndarray) -> list[LayoutRegion]:
        ...


class TextDetector(ABC):
    @abstractmethod
    def detect_text(self, image: np.ndarray) -> list[TextBox]:
        ...


class TextRecognizer(ABC):
    @abstractmethod
    def recognize(self, image: np.ndarray,
                  boxes: list[TextBox]) -> list[TextLine]:
        ...


class OCREngine(ABC):
    """엔드투엔드 엔진. 웹이 직접 호출하는 단위."""
    name: str = "base"
    capabilities: set[str] = set()  # {"layout","detection","recognition","table","seal"}

    @abstractmethod
    def run(self, image: np.ndarray, opts: EngineOptions) -> OCRResult:
        ...

    def warmup(self) -> None:
        """모델 로드(최초 1회). 지연 로딩 엔진은 override."""
        return None
