"""B롤 자리는 실패가 아니라 빈 칸이다 — 캡컷 초안에서 원본을 회수한다.

롱폼 자동편집기의 broll 씬은 롱폼 폴더에 파일이 0개다 (사람이 캡컷에서 직접
화면 녹화를 얹는 자리라서 그렇다). 대신 그 롱폼의 캡컷 초안(draft_content.json)
안에 원본 경로와 자른 구간이 그대로 남아 있으므로 그걸 읽어 되찾는다.

시간축 주의: 초안 시간과 나레이션 시간은 다르다(도입부 삽입 등으로 밀린다).
나레이션 오디오 세그먼트들의 (target_timerange, source_timerange) 쌍을 그대로
읽어 "나레이션 시간 → 초안 시간" 대응표를 만들고, 그 표로 변환해야 한다.
캡컷은 시간을 마이크로초로 적는다.

모션그래픽(mg_*.mov) 합성 시 알파가 중간에 깨지지 않도록 필터 맨 앞에
`format=rgba` 를 반드시 넣는다 (format=yuv420p 가 끼면 검게 뭉개진다, 실측).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import find_tool, get_logger

logger = get_logger()

GREEN_LOW = 0.10
GREEN_HIGH = 0.85
CHROMA_FILTER = "chromakey=0x00FF00:0.16:0.04"


class BrollError(Exception):
    pass


def build_narration_offset_table(draft_content: Dict[str, Any]) -> List[Tuple[int, int, int]]:
    """초안의 나레이션 오디오 트랙에서 (target_start, target_end, offset_to_source) 표를 만든다.

    offset_to_source = source_start - target_start (마이크로초). 이 표로
    "나레이션(narration.mp3) 위 시간" → "초안(원본 타임라인) 위 시간" 을 변환한다.
    """
    table = []
    for track in draft_content.get("tracks", []):
        if track.get("type") != "audio":
            continue
        for seg in track.get("segments", []):
            tr = seg.get("target_timerange", {})
            sr = seg.get("source_timerange", {})
            if not tr or not sr:
                continue
            t_start, t_dur = tr.get("start", 0), tr.get("duration", 0)
            s_start = sr.get("start", 0)
            table.append((t_start, t_start + t_dur, s_start - t_start))
    table.sort(key=lambda x: x[0])
    return table


def narration_time_to_draft_time(offset_table: List[Tuple[int, int, int]], narration_us: int) -> Optional[int]:
    """나레이션 위 시간(마이크로초)을 초안(원본) 타임라인 시간으로 바꾼다."""
    for t_start, t_end, offset in offset_table:
        if t_start <= narration_us <= t_end:
            return narration_us + offset
    # 표 밖이면 가장 가까운 구간의 오프셋을 그대로 적용 (근사치)
    if offset_table:
        closest = min(offset_table, key=lambda x: min(abs(x[0] - narration_us), abs(x[1] - narration_us)))
        return narration_us + closest[2]
    return None


def find_source_clip_at_time(draft_content: Dict[str, Any], draft_time_us: int) -> Optional[Dict[str, Any]]:
    """초안의 영상 트랙에서 그 시각에 있는 클립의 원본 경로/구간을 찾는다."""
    materials_by_id = {m["id"]: m for m in draft_content.get("materials", {}).get("videos", [])}
    for track in draft_content.get("tracks", []):
        if track.get("type") != "video":
            continue
        for seg in track.get("segments", []):
            tr = seg.get("target_timerange", {})
            start, dur = tr.get("start", 0), tr.get("duration", 0)
            if start <= draft_time_us <= start + dur:
                mat = materials_by_id.get(seg.get("material_id"))
                if not mat:
                    continue
                sr = seg.get("source_timerange", {})
                return {
                    "path": mat.get("path"),
                    "source_start_us": sr.get("start", 0) + (draft_time_us - start),
                    "clip_start_us": start,
                    "clip_end_us": start + dur,
                }
    return None


def load_draft_content(draft_json_path: str) -> Dict[str, Any]:
    with open(draft_json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _sample_green_ratio(video_path: str, at_seconds: float) -> float:
    """지정 시각에서 프레임 하나를 떠서 초록 비율을 잰다. 그 길이 안에서만 떠야 한다(함정)."""
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise BrollError("ffmpeg 를 찾을 수 없습니다.")

    import tempfile
    from PIL import Image
    import numpy as np

    with tempfile.TemporaryDirectory() as td:
        out_png = Path(td) / "frame.png"
        cmd = [ffmpeg, "-y", "-ss", f"{max(0.0, at_seconds):.3f}", "-i", video_path,
               "-frames:v", "1", str(out_png)]
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
        if proc.returncode != 0 or not out_png.exists():
            return 0.0  # 뜨지 못했으면(길이 초과 등) 내용 없음으로 취급
        img = np.asarray(Image.open(out_png).convert("RGB"))
        r, g, b = img[:, :, 0].astype(int), img[:, :, 1].astype(int), img[:, :, 2].astype(int)
        green_mask = (g > 100) & (g > r + 30) & (g > b + 30)
        return float(green_mask.mean())


def find_content_bounds(video_path: str, clip_duration_seconds: float, step: float = 0.5) -> Optional[Tuple[float, float]]:
    """통짜 초록 구간을 앞뒤로 훑어, 내용이 있는 구간(초록 아닌 구간)을 찾는다."""
    t = 0.0
    start, end = None, None
    while t < clip_duration_seconds:
        ratio = _sample_green_ratio(video_path, t)
        if ratio <= GREEN_HIGH:
            if start is None:
                start = t
            end = t
        t += step
    if start is None:
        return None
    return start, min(clip_duration_seconds, end + step)


def extract_broll_clip(
    video_path: str, out_path: Path, start_seconds: float, duration_seconds: float,
    brand_color_hex: str,
) -> Dict[str, Any]:
    """B롤 원본을 잘라 회수한다. 초록 화면(크로마키)이면 빼고 브랜드색 배경에 겹친다."""
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise BrollError("ffmpeg 를 찾을 수 없습니다.")

    ratio = _sample_green_ratio(video_path, start_seconds + duration_seconds / 2)
    even_w, even_h = 1080, 1920

    if ratio > GREEN_HIGH:
        raise BrollError("이 구간은 화면 전체가 초록(크로마키)이라 쓸 내용이 없습니다.")

    if ratio < GREEN_LOW:
        # 크로마키 아님: 그대로 크롭해서 회수 (세로 배치는 이후 user_media 단계가 처리)
        filt = f"format=rgba,scale={even_w}:-2"
        cmd = [ffmpeg, "-y", "-ss", f"{start_seconds:.3f}", "-t", f"{duration_seconds:.3f}",
               "-i", video_path, "-vf", filt, "-c:v", "prores_ks", "-profile:v", "4",
               "-pix_fmt", "yuva444p10le", "-crf", "16", str(out_path)]
    else:
        # 크로마키 구간: 초록을 빼고 브랜드색 배경 위에 겹친다.
        color = brand_color_hex.lstrip("#")
        filt = (
            f"[0:v]format=rgba,{CHROMA_FILTER}[fg];"
            f"color=c=0x{color}:s={even_w}x{even_h}[bg];"
            f"[bg][fg]overlay=shortest=1,scale={even_w}:{even_h}"
        )
        cmd = [ffmpeg, "-y", "-ss", f"{start_seconds:.3f}", "-t", f"{duration_seconds:.3f}",
               "-i", video_path, "-filter_complex", filt, "-crf", "16", str(out_path)]

    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise BrollError(f"B롤 회수 실패: {proc.stderr.decode('utf-8','ignore')[:300]}")
    return {"path": str(out_path), "chroma_removed": ratio >= GREEN_LOW}


def composite_motion_graphic(mg_path: str, bg_glow_path: str, out_path: Path) -> Dict[str, Any]:
    """모션그래픽(mg_*.mov, 알파 있음)을 bg_glow 배경 위에 합성한다.

    필터 맨 앞에 format=rgba 를 둬야 한다 — 중간에 yuv420p 가 끼면 검게 뭉개진다(실측).
    """
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise BrollError("ffmpeg 를 찾을 수 없습니다.")

    filt = (
        "[0:v]format=rgba,scale=1080:-2[bg];"
        "[1:v]format=rgba,scale=1080:-2[fg];"
        "[bg][fg]overlay=shortest=1:format=auto"
    )
    cmd = [
        ffmpeg, "-y", "-stream_loop", "-1", "-i", bg_glow_path, "-i", mg_path,
        "-filter_complex", filt, "-shortest", "-crf", "16", str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise BrollError(f"모션그래픽 합성 실패: {proc.stderr.decode('utf-8','ignore')[:300]}")
    return {"path": str(out_path)}
