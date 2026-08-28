"""자막 표기 사전 + 줄 길이 분할.

실측 공식: 한 줄에 들어가는 글자 수 = 1080 / (7.2 x 글씨크기).
MAX_CHARS 와 띄어쓰기 분할 기준을 반드시 같은 값으로 맞춘다 (compute_max_chars 하나만 쓴다).
"""
from __future__ import annotations

from typing import Any, Dict, List

# 줄 끝에 남으면 안 되는 수관형사류. 이 낱말 뒤에서 줄을 끊으면 다음 낱말과의
# 연결이 끊겨 어색해진다 (예: "딱 한" / "번만").
QUANT_PREFIXES = {"한", "두", "세", "네", "몇", "약", "총", "단", "이", "그", "저", "딱"}

# 자막에 표시할 때만 다듬는 표기 사전 (실제 발음/TTS 입력은 건드리지 않는다)
DISPLAY_REPLACEMENTS = {
    "&": "그리고",
}


def compute_max_chars(font_size: float) -> int:
    """실측 공식: 1080 / (7.2 * 글씨크기). 최소 4자는 보장한다."""
    return max(4, int(1080 // (7.2 * font_size)))


def apply_display_dict(text: str) -> str:
    out = text
    for k, v in DISPLAY_REPLACEMENTS.items():
        out = out.replace(k, v)
    return out


def _wrap_tokens(tokens: List[str], max_chars: int) -> List[List[str]]:
    """토큰 리스트를 max_chars 이내 줄들로 나눈다. 수관형사 뒤 줄바꿈은 피한다."""
    tokens = list(tokens)
    lines: List[List[str]] = []
    i = 0
    n = len(tokens)
    guard = 0
    while i < n:
        guard += 1
        if guard > 10000:
            # 안전장치: 무한루프 방지
            lines.append(tokens[i:])
            break
        cur: List[str] = []
        cur_len = 0
        j = i
        while j < n:
            tok = tokens[j]
            add_len = len(tok) + (1 if cur else 0)
            if cur_len + add_len > max_chars:
                break
            cur.append(tok)
            cur_len += add_len
            j += 1

        if not cur:
            # 낱말 하나가 max_chars 보다 긴 경우: 강제로 잘라 다음 줄로 넘긴다.
            tok = tokens[j]
            cur = [tok[:max_chars]]
            remainder = tok[max_chars:]
            if remainder:
                tokens[j] = remainder
            else:
                j += 1
            lines.append(cur)
            i = j
            continue

        # 다음에 이어질 낱말이 더 있는데(=문장 중간에서 끊긴 것) 줄 끝이
        # 수관형사류면 한 낱말 물러서서 다음 줄로 같이 넘긴다.
        while len(cur) > 1 and cur[-1] in QUANT_PREFIXES and j < n:
            cur.pop()

        lines.append(cur)
        i += len(cur)
    return lines


def wrap_sentence(
    sentence_text: str,
    word_timestamps: List[Dict[str, float]],
    max_chars: int,
    sentence_start: float,
    sentence_end: float,
) -> List[Dict[str, Any]]:
    """한 문장을 자막 줄로 나누고 각 줄에 실측 타이밍을 배분한다.

    word_timestamps 는 sentence_text.split() 과 같은 개수/순서라고 가정한다
    (align.py 가 그렇게 맞춰서 넘긴다). 개수가 어긋나면 균등 배분으로 대체한다.
    """
    tokens = sentence_text.split()
    if not tokens:
        return []

    line_token_groups = _wrap_tokens(tokens, max_chars)

    if len(word_timestamps) != len(tokens):
        # 방어적 fallback: 낱말 시간과 개수가 안 맞으면 글자 수 비율로 균등 배분
        total = sentence_end - sentence_start
        total_chars = sum(len("".join(g)) for g in line_token_groups) or 1
        out = []
        cursor = sentence_start
        for g in line_token_groups:
            text = " ".join(g)
            portion = total * (len("".join(g)) / total_chars)
            start, end = cursor, cursor + portion
            out.append({"text": apply_display_dict(text), "start": start, "end": end})
            cursor = end
        if out:
            out[-1]["end"] = sentence_end
        return out

    # 토큰별 시작/끝 시각을 그대로 있는 낱말 타임스탬프에서 가져와 줄 경계를 만든다.
    out = []
    tok_idx = 0
    for g_i, g in enumerate(line_token_groups):
        n_tok = len(g)
        first_w = word_timestamps[tok_idx]
        last_w = word_timestamps[tok_idx + n_tok - 1]
        start = first_w["start"]
        end = last_w["end"]
        out.append({"text": apply_display_dict(" ".join(g)), "start": start, "end": end})
        tok_idx += n_tok

    # 줄 사이에 빈틈이 없도록: 각 줄의 끝을 다음 줄의 시작으로 맞춘다.
    for k in range(len(out) - 1):
        out[k]["end"] = out[k + 1]["start"]
    if out:
        out[-1]["end"] = max(out[-1]["end"], sentence_end)
        out[0]["start"] = min(out[0]["start"], sentence_start)
    return out


def build_subtitle_lines(timing: Dict[str, Any], font_size: float) -> List[Dict[str, Any]]:
    """timing.json 전체를 훑어 자막 줄 목록(초 단위)을 만든다."""
    max_chars = compute_max_chars(font_size)
    lines: List[Dict[str, Any]] = []
    for sent in timing.get("sentences", []):
        sent_lines = wrap_sentence(
            sent["text"], sent.get("words", []), max_chars, sent["start"], sent["end"]
        )
        lines.extend(sent_lines)
    return lines


def to_microsecond_segments(lines: List[Dict[str, Any]], total_duration_seconds: float) -> List[Dict[str, Any]]:
    """초 단위 줄 목록을 마이크로초 정수 경계로 바꾼다 (겹침/틈 방지, 함정 2-2).

    각 경계를 한 번씩만 정수로 반올림하고, 그 경계값을 앞줄의 끝과 뒷줄의 시작에
    그대로 재사용한다 — 두 번 계산하면 반올림 오차로 1us 겹치거나 빈다.
    """
    if not lines:
        return []
    boundaries = [round(lines[0]["start"] * 1_000_000)]
    for ln in lines:
        boundaries.append(round(ln["end"] * 1_000_000))
    total_us = round(total_duration_seconds * 1_000_000)
    boundaries[-1] = min(boundaries[-1], total_us) if total_us > 0 else boundaries[-1]

    segments = []
    for i, ln in enumerate(lines):
        start_us = boundaries[i]
        end_us = boundaries[i + 1]
        if end_us <= start_us:
            end_us = start_us + 1  # 최소 길이 보장 (0 이하 방지)
        segments.append({"text": ln["text"], "start_us": start_us, "end_us": end_us})
    return segments
