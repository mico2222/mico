"""나레이션 mp3 를 문장별 타이밍(timing.json)으로 정렬한다.

faster-whisper 를 우선 시도한다. 윈도우에서 Visual C++ 재배포 패키지가 없으면
불러오다가 죽으므로 반드시 지연 임포트하고, 실패하면 글자 수 비례 배분으로
대체한다 (정확도는 떨어지지만 프로그램이 멈추지는 않는다).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .common import get_logger, run_ffprobe

logger = get_logger()

VC_REDIST_MSG = (
    "받아쓰기 도구(faster-whisper)를 불러오지 못해 자막 시간을 글자 수 비율로 추정했습니다. "
    "정확한 자막 타이밍을 쓰려면 Visual C++ 재배포 패키지를 설치하세요: "
    "https://aka.ms/vs/17/release/vc_redist.x64.exe (설치 후 프로그램을 다시 켜주세요.)"
)


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"[.!?\n]+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _proportional_fallback(text: str, total_seconds: float) -> Dict[str, Any]:
    sentences = _split_sentences(text)
    total_chars = sum(len(s) for s in sentences) or 1
    result_sentences = []
    cursor = 0.0
    for i, s in enumerate(sentences):
        dur = total_seconds * (len(s) / total_chars)
        start, end = cursor, cursor + dur
        words = s.split()
        w_count = len(words) or 1
        w_dur = dur / w_count
        word_list = []
        wcur = start
        for w in words:
            word_list.append({"text": w, "start": round(wcur, 3), "end": round(wcur + w_dur, 3)})
            wcur += w_dur
        result_sentences.append({
            "index": i, "text": s, "start": round(start, 3), "end": round(end, 3), "words": word_list,
        })
        cursor = end
    return {"engine": "estimated", "total_seconds": total_seconds, "sentences": result_sentences,
            "warnings": [VC_REDIST_MSG]}


def _whisper_align(mp3_path: str, text: str, total_seconds: float) -> Dict[str, Any]:
    from faster_whisper import WhisperModel  # 지연 임포트

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(mp3_path, language="ko", word_timestamps=True)

    all_words: List[Dict[str, float]] = []
    for seg in segments:
        for w in (seg.words or []):
            all_words.append({"text": w.word.strip(), "start": w.start, "end": w.end})

    sentences = _split_sentences(text)
    if not all_words or not sentences:
        return _proportional_fallback(text, total_seconds)

    # 문장별 낱말 개수 비율로 whisper 낱말 타임스탬프를 나눠 배정한다.
    # (whisper가 인식한 글자와 원문 대본이 완전히 같지 않을 수 있어, 정확한 문자열
    #  매칭 대신 "낱말 개수 비율" 로 강건하게 맞춘다.)
    sent_word_counts = [max(1, len(s.split())) for s in sentences]
    total_sent_words = sum(sent_word_counts)
    total_whisper_words = len(all_words)

    result_sentences = []
    w_idx = 0
    consumed_ratio = 0.0
    for i, s in enumerate(sentences):
        consumed_ratio += sent_word_counts[i] / total_sent_words
        target_idx = min(total_whisper_words, round(consumed_ratio * total_whisper_words))
        target_idx = max(target_idx, w_idx + 1)
        target_idx = min(target_idx, total_whisper_words)
        chunk = all_words[w_idx:target_idx] or [all_words[min(w_idx, total_whisper_words - 1)]]
        start = chunk[0]["start"]
        end = chunk[-1]["end"]
        words_out = s.split()
        if len(words_out) == len(chunk):
            word_list = [{"text": wt, "start": round(c["start"], 3), "end": round(c["end"], 3)}
                         for wt, c in zip(words_out, chunk)]
        else:
            # 낱말 개수가 어긋나면 원문 낱말 기준으로 구간 시간을 균등 배분한다.
            span = end - start
            n = len(words_out) or 1
            word_list = []
            for j, wt in enumerate(words_out):
                ws = start + span * j / n
                we = start + span * (j + 1) / n
                word_list.append({"text": wt, "start": round(ws, 3), "end": round(we, 3)})
        result_sentences.append({"index": i, "text": s, "start": round(start, 3), "end": round(end, 3), "words": word_list})
        w_idx = target_idx

    return {"engine": "whisper", "total_seconds": total_seconds, "sentences": result_sentences, "warnings": []}


def align_narration(mp3_path: str, script_text: str) -> Dict[str, Any]:
    try:
        info = run_ffprobe(mp3_path)
        total_seconds = info["duration"] or estimate_len(script_text)
    except Exception:
        total_seconds = estimate_len(script_text)

    try:
        result = _whisper_align(mp3_path, script_text, total_seconds)
        return result
    except ImportError:
        logger.warning(VC_REDIST_MSG)
        return _proportional_fallback(script_text, total_seconds)
    except OSError as e:
        # 윈도우에서 DLL(Visual C++ 재배포 패키지) 누락 시 여기로 떨어진다.
        logger.warning("faster-whisper 로딩 실패(DLL 문제로 추정): %s", e)
        return _proportional_fallback(script_text, total_seconds)
    except Exception:  # noqa: BLE001
        logger.exception("faster-whisper 정렬 실패, 추정치로 대체")
        return _proportional_fallback(script_text, total_seconds)


def estimate_len(text: str, chars_per_sec: float = 7.3) -> float:
    clean = re.sub(r"\s+", "", text.strip())
    return max(1.0, len(clean) / chars_per_sec) if chars_per_sec else 1.0
