"""대본 → 나레이션 mp3.

구글 키가 아직 없으면 무음 mp3 로 길이만 맞춰서 최소 동작본을 완성할 수 있게 한다.
google-cloud-texttospeech 는 실제로 쓸 때만 지연 임포트한다.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict

from .common import find_tool, get_logger, run_ffprobe

logger = get_logger()


def estimate_seconds(text: str, chars_per_sec: float = 7.3) -> float:
    clean = re.sub(r"\s+", "", text.strip())
    if not clean or chars_per_sec <= 0:
        return 1.0
    return max(1.0, len(clean) / chars_per_sec)


def synth_silence(out_path: Path, seconds: float) -> None:
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg 를 찾을 수 없어 무음 파일을 만들 수 없습니다.")
    seconds = max(1.0, seconds)
    cmd = [
        ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", f"{seconds:.2f}", "-q:a", "9", "-acodec", "libmp3lame", str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"무음 mp3 생성 실패: {proc.stderr.decode('utf-8','ignore')[:300]}")


def _translate_google_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "could not automatically determine credentials" in msg or "application default credentials" in msg:
        return "구글 인증 정보를 찾지 못했습니다. env.txt 에 GOOGLE_APPLICATION_CREDENTIALS 경로가 맞는지 확인해주세요."
    if "permission" in msg or "403" in msg:
        return "구글 계정에 Text-to-Speech 사용 권한이 없습니다. Vertex AI/Cloud TTS API 사용 설정을 확인해주세요."
    if "billing" in msg:
        return "구글 클라우드 결제 계정이 연결되지 않았습니다. 결제 설정을 확인해주세요."
    if "quota" in msg or "429" in msg:
        return "구글 TTS 사용량 한도를 넘었습니다. 잠시 후 다시 시도해주세요."
    return f"구글 TTS 호출 중 문제가 발생했습니다: {str(exc)[:200]}"


def synth_narration(text: str, out_path: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """성공하면 실제 목소리, 실패하면 같은 길이의 무음으로 대신 채운다."""
    tts_cfg = cfg.get("tts", {})
    chars_per_sec = tts_cfg.get("chars_per_sec", 7.3)
    est = estimate_seconds(text, chars_per_sec)

    try:
        from google.cloud import texttospeech  # 지연 임포트
    except ImportError:
        warn = "google-cloud-texttospeech 패키지가 설치되지 않아 무음으로 대신합니다. [도구 설치]를 눌러주세요."
        logger.warning(warn)
        synth_silence(out_path, est)
        return {"path": str(out_path), "seconds": est, "engine": "silence", "warning": warn}

    try:
        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="ko-KR", name=tts_cfg.get("voice_name", "ko-KR-Neural2-C"),
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=tts_cfg.get("speaking_rate", 1.0),
        )
        response = client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )
        with open(out_path, "wb") as f:
            f.write(response.audio_content)
    except Exception as e:  # noqa: BLE001 - 외부 API 오류를 폭넓게 잡아 한국어로 안내
        logger.exception("google TTS 실패")
        warn = _translate_google_error(e)
        synth_silence(out_path, est)
        return {"path": str(out_path), "seconds": est, "engine": "silence", "warning": warn}

    try:
        info = run_ffprobe(str(out_path))
        seconds = info["duration"] or est
    except Exception:
        seconds = est

    return {"path": str(out_path), "seconds": seconds, "engine": "google", "warning": None}
