"""pycapcut 로 캡컷 초안(draft)을 조립한다.

핵심 함정 대응:
- 오디오 소재는 영상 트랙이 있는 mp4 를 거부한다 (ValueError: 音频素材不应包含视频轨道)
  → tts.py/ffmpeg 에서 이미 순수 오디오만 넘기지만, 여기서도 방어적으로 안내한다.
- 소재 길이는 ffprobe 가 아니라 pycapcut(pymediainfo) 이 잰 값을 그대로 쓴다.
- 시간 계산은 전부 정수 마이크로초로 한다 (초 단위 float 누적 금지).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .common import get_logger
from .subtitle_text import build_subtitle_lines, to_microsecond_segments
from .user_media import MIN_ASPECT, crop_for_mode

logger = get_logger()

SEC = 1_000_000


class AssembleError(Exception):
    """한국어 메시지를 담는 예외."""


def _import_pycapcut():
    try:
        import pycapcut
        return pycapcut
    except ImportError as e:
        raise AssembleError(
            "pycapcut 패키지가 설치되지 않았습니다. [도구 설치] 버튼을 눌러 설치해주세요."
        ) from e


def _hex_to_rgb01(hex_color: str):
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        r, g, b = (int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        return (r, g, b)
    return (1.0, 1.0, 1.0)


def _hex_to_capcut_color_alpha(hex_color: str) -> str:
    """캡컷 배경색 문자열: '#RRGGBBAA' 형식."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        return f"#{hex_color}FF"
    return "#000000FF"


def ensure_solid_background(cfg: Dict[str, Any], job_dir: Path) -> Path:
    """브랜드 색 1080x1920 배경 이미지를 만들어 캐시한다."""
    from PIL import Image  # 지연 임포트 (없으면 아래에서 한국어 안내)

    out_path = job_dir / "bg_solid.png"
    if out_path.exists():
        return out_path
    color_hex = cfg.get("brand", {}).get("primary_color", "#111111")
    rgb = tuple(int(v * 255) for v in _hex_to_rgb01(color_hex))
    img = Image.new("RGB", (1080, 1920), rgb)
    img.save(out_path)
    return out_path


def _make_video_segment(pycapcut, path: str, start_us: int, target_us: int, crop_box):
    VideoMaterial = pycapcut.VideoMaterial
    VideoSegment = pycapcut.VideoSegment
    Timerange = pycapcut.Timerange
    CropSettings = pycapcut.CropSettings

    crop_settings = CropSettings(**crop_box.as_corners())
    material = VideoMaterial(path, crop_settings=crop_settings)

    if material.duration >= target_us:
        source_tr = Timerange(0, target_us)
        speed = 1.0
    else:
        # 소재가 배정된 시간보다 짧으면 속도를 늦춰 전체 구간을 채운다.
        source_tr = Timerange(0, material.duration)
        speed = material.duration / target_us if target_us > 0 else 1.0

    seg = VideoSegment(
        material, Timerange(start_us, target_us),
        source_timerange=source_tr, speed=speed,
    )
    return seg


def build_draft(
    job_dir: Path,
    draft_folder: str,
    draft_name: str,
    narration_mp3: str,
    timing: Dict[str, Any],
    scenes: List[Dict[str, Any]],
    scene_media: Dict[int, Dict[str, Any]],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """초안을 만들어 저장한다. scene_media: {scene_id: {"path":..., "mode":..., "aspect":..., "pos_x":..., "pos_y":...}}"""
    pycapcut = _import_pycapcut()

    DraftFolder = pycapcut.DraftFolder
    TrackType = pycapcut.TrackType
    Timerange = pycapcut.Timerange
    AudioSegment = pycapcut.AudioSegment
    AudioMaterial = pycapcut.AudioMaterial
    TextSegment = pycapcut.TextSegment
    TextStyle = pycapcut.TextStyle
    TextBorder = pycapcut.TextBorder
    CropSettings = pycapcut.CropSettings

    folder = DraftFolder(draft_folder)
    script = folder.create_draft(draft_name, 1080, 1920, 30, allow_replace=True)

    # --- 오디오: 나레이션 ---
    try:
        audio_material = AudioMaterial(narration_mp3)
    except ValueError as e:
        raise AssembleError(
            "나레이션 파일이 오디오가 아니라 영상으로 인식됐습니다. "
            "ffmpeg -vn 으로 소리만 뽑아 다시 시도해주세요."
        ) from e
    total_us = audio_material.duration  # ffprobe 가 아니라 pycapcut 이 잰 값을 신뢰한다.

    script.add_track(TrackType.audio, "나레이션")
    script.add_segment(
        AudioSegment(audio_material, Timerange(0, total_us)), "나레이션"
    )

    # --- 배경(맨 아래 비디오 트랙, 반드시 0초부터 시작) ---
    script.add_track(TrackType.video, "배경", relative_index=0)
    bg_path = ensure_solid_background(cfg, job_dir)
    bg_crop = CropSettings()  # 자르지 않음, 이미 1080x1920 이라 100% 꽉 참
    bg_material = pycapcut.VideoMaterial(str(bg_path), crop_settings=bg_crop)
    bg_seg = pycapcut.VideoSegment(bg_material, Timerange(0, total_us))
    script.add_segment(bg_seg, "배경")

    # --- 씬 영상/이미지 (배경 위) ---
    script.add_track(TrackType.video, "장면", relative_index=1)
    placement_cfg = cfg.get("placement", {})
    for sc in scenes:
        media = scene_media.get(sc["id"])
        if not media or not media.get("path"):
            continue  # 아직 재료 없는 빈 씬 (검수에서 채운다)
        path = media["path"]
        mode = media.get("mode", placement_cfg.get("default_mode", "fill"))
        aspect = media.get("aspect", placement_cfg.get("default_aspect", MIN_ASPECT))
        pos_x = media.get("pos_x", placement_cfg.get("default_pos_x", 0.5))
        pos_y = media.get("pos_y", placement_cfg.get("default_pos_y", 0.5))

        try:
            from .common import run_ffprobe
            info = run_ffprobe(path)
            src_w, src_h = info["width"], info["height"]
        except Exception:
            src_w, src_h = 1920, 1080

        crop_box, _pct = crop_for_mode(src_w, src_h, mode, aspect, pos_x, pos_y)

        start_us = round(sc["start"] * SEC)
        end_us = min(round(sc["end"] * SEC), total_us)
        target_us = max(1, end_us - start_us)

        try:
            seg = _make_video_segment(pycapcut, path, start_us, target_us, crop_box)
        except Exception:
            logger.exception("씬 %s 영상 배치 실패", sc["id"])
            continue

        if mode == "full":
            color = _hex_to_capcut_color_alpha(cfg.get("brand", {}).get("primary_color", "#111111"))
            seg.add_background_filling("color", color=color)

        script.add_segment(seg, "장면")

    # --- 자막 ---
    sub_cfg = cfg.get("subtitle", {})
    font_size = sub_cfg.get("font_size", 14)
    lines = build_subtitle_lines(timing, font_size)
    segments = to_microsecond_segments(lines, total_us / SEC)

    script.add_track(TrackType.text, "자막")
    color = _hex_to_rgb01(sub_cfg.get("color", "#FFFFFF"))
    border_color = _hex_to_rgb01(sub_cfg.get("outline_color", "#000000"))
    style = TextStyle(size=font_size, color=color, align=1, auto_wrapping=False)
    border = TextBorder(color=border_color, width=40.0)
    clip_settings = pycapcut.ClipSettings(transform_y=sub_cfg.get("place_y", -0.72))

    for seg_info in segments:
        if seg_info["end_us"] <= seg_info["start_us"]:
            continue
        text_seg = TextSegment(
            seg_info["text"],
            Timerange(seg_info["start_us"], seg_info["end_us"] - seg_info["start_us"]),
            style=style, border=border, clip_settings=clip_settings,
        )
        script.add_segment(text_seg, "자막")

    script.save()
    draft_path = str(Path(draft_folder) / draft_name)
    return {"draft_path": draft_path, "duration_seconds": total_us / SEC}
