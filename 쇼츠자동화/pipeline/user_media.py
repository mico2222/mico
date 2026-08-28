"""세로 배치기 — 이 프로그램의 심장.

캡컷(pycapcut) 은 소스를 실제로 잘라내지 않는다. 소재의 `crop_settings`
(0~1 정규화 좌표, 좌상단이 원점)로 "보여줄 영역"만 정하면, 캡컷이 그 영역을
캔버스에 맞춰(가로/세로 중 더 작은 배율로 "contain" 방식) 자동으로 배치한다.
그래서 화면 점유율은 확대 배율(scale)이 아니라 크롭 영역의 가로세로비로 정해진다.

실측 공식(占有율 표)의 근거:
    occupied_height_pct = min(1080/aspect, 1920) / 1920 * 100
    (aspect = 크롭 영역의 가로/세로 비율. 1080x1920 캔버스 기준)

- fill : 크롭 비율을 9:16(0.5625)으로 고정 → 100% 꽉 채움. 인물/그림에 안전.
- full : 크롭하지 않음(원본 비율 그대로) → 남는 위아래는 캡컷 배경 채우기로 처리.
- zoom : 원하는 비율로 크롭. 하한은 반드시 0.5625 (그 밑으로 내려도 더 커지지 않고
         오히려 좌우가 남는다).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Tuple

MIN_ASPECT = 0.5625  # 9:16. 이보다 낮게 잡지 말 것 (물리 법칙 문서 참고)

Mode = Literal["fill", "full", "zoom"]


@dataclass
class CropBox:
    """크롭 영역. 0~1 정규화 좌표, 좌상단 원점."""
    x0: float
    y0: float
    x1: float
    y1: float

    def as_corners(self) -> dict:
        return {
            "upper_left_x": self.x0, "upper_left_y": self.y0,
            "upper_right_x": self.x1, "upper_right_y": self.y0,
            "lower_left_x": self.x0, "lower_left_y": self.y1,
            "lower_right_x": self.x1, "lower_right_y": self.y1,
        }


def occupied_height_pct(aspect: float, canvas_w: int = 1080, canvas_h: int = 1920) -> float:
    """이 가로세로비로 크롭했을 때, 세로 화면에서 차지하는 높이 비율(%)."""
    if aspect <= 0:
        return 0.0
    displayed_h = min(canvas_w / aspect, canvas_h)
    return displayed_h / canvas_h * 100.0


def compute_crop(
    src_w: int, src_h: int, aspect: float, center_x: float = 0.5, center_y: float = 0.5,
) -> CropBox:
    """소스 안에서 지정한 중심에서 지정한 비율로 자를 영역(0~1 정규화)을 계산한다.

    "cover" 방식: 그 비율의 사각형이 소스 안에 들어가는 최대 크기로 잡는다.
    """
    src_aspect = src_w / src_h
    if aspect <= src_aspect:
        crop_h_px = src_h
        crop_w_px = aspect * crop_h_px
    else:
        crop_w_px = src_w
        crop_h_px = crop_w_px / aspect

    crop_w_px = min(crop_w_px, src_w)
    crop_h_px = min(crop_h_px, src_h)

    cx_px = center_x * src_w
    cy_px = center_y * src_h

    x0_px = cx_px - crop_w_px / 2
    y0_px = cy_px - crop_h_px / 2
    x0_px = max(0.0, min(x0_px, src_w - crop_w_px))
    y0_px = max(0.0, min(y0_px, src_h - crop_h_px))

    x0 = x0_px / src_w
    y0 = y0_px / src_h
    x1 = (x0_px + crop_w_px) / src_w
    y1 = (y0_px + crop_h_px) / src_h
    return CropBox(x0, y0, x1, y1)


def crop_for_mode(
    src_w: int, src_h: int, mode: Mode,
    aspect: float = MIN_ASPECT, center_x: float = 0.5, center_y: float = 0.5,
) -> Tuple[CropBox, float]:
    """모드별 크롭 영역과 화면 점유율(%)을 계산한다."""
    if mode == "fill":
        target_aspect = MIN_ASPECT
    elif mode == "full":
        target_aspect = src_w / src_h
    else:  # zoom
        target_aspect = max(MIN_ASPECT, aspect)

    box = compute_crop(src_w, src_h, target_aspect, center_x, center_y)
    pct = occupied_height_pct(target_aspect)
    return box, pct


def nudge_position(
    center: float, crop_len_frac: float, direction: int, percent: float,
) -> float:
    """미세/보통 이동. `direction`은 +1(화면에서 오른쪽/아래로 보이길 원함) 또는 -1.

    실측 규칙: 이동량 = 비율 x (잘라낸 폭(또는 높이) / 원본 폭(또는 높이)).
    화면에서 보이는 대상이 원하는 방향으로 움직이려면, 크롭창(보는 영역)은
    반대 방향으로 움직여야 한다 — 그래서 부호를 뒤집는다.
    """
    delta = percent * crop_len_frac
    new_center = center - direction * delta  # 부호 반전
    lo = crop_len_frac / 2
    hi = 1 - crop_len_frac / 2
    if lo > hi:
        lo, hi = hi, lo
    return max(lo, min(hi, new_center))


def center_position() -> Tuple[float, float]:
    return 0.5, 0.5


def even(n: float) -> int:
    """홀수 해상도로 자르면 ffmpeg 이 말없이 줄인다 (함정 #12). 항상 짝수로 맞춘다."""
    v = int(round(n))
    return v if v % 2 == 0 else v - 1
