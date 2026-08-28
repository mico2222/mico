"""대본 → 씬 분할(scene_plan.json). LLM 호출은 여기 한 곳뿐이다.

AI가 준 시간 정보는 절대 쓰지 않는다. timing.json 에서 실측한 문장 시작/끝을
기준으로 씬의 시작/끝을 다시 계산한다 (LLM은 산수를 틀린다).
"""
from __future__ import annotations

from typing import Any, Dict, List

from .claude_cli import ClaudeCliError, run_claude_prompt
from .common import PROMPTS_DIR, get_logger

logger = get_logger()

PROMPT_TEMPLATE_PATH = PROMPTS_DIR / "scene_plan_shorts.md"


def _build_prompt(script_text: str, sentences: List[str], cfg: Dict[str, Any]) -> str:
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    sentence_lines = "\n".join(f"{i}: {s}" for i, s in enumerate(sentences))
    channel = cfg.get("channel", {})
    has_sheet = "있음" if cfg.get("character", {}).get("use_face") else "없음"
    filled = (
        template.replace("{{SCRIPT}}", script_text)
        .replace("{{SENTENCES}}", sentence_lines)
        .replace("{{CHANNEL_TOPIC}}", f"{channel.get('name','')} / {channel.get('topic','')}")
        .replace("{{AUDIENCE}}", channel.get("audience", ""))
        .replace("{{HAS_CHARACTER_SHEET}}", has_sheet)
    )
    return filled


def _validate_and_fix_coverage(scenes: List[Dict[str, Any]], n_sentences: int) -> List[Dict[str, Any]]:
    """모든 문장 번호가 정확히 한 번씩 커버되도록 보정한다."""
    covered = set()
    for sc in scenes:
        covered.update(sc.get("sentence_indices", []))
    missing = [i for i in range(n_sentences) if i not in covered]
    if missing:
        logger.warning("씬 분할에서 빠진 문장 번호 발견, 마지막 씬에 붙임: %s", missing)
        if scenes:
            scenes[-1]["sentence_indices"] = sorted(set(scenes[-1].get("sentence_indices", [])) | set(missing))
        else:
            scenes.append({
                "id": 1, "sentence_indices": missing,
                "image_prompt": "화면을 가득 채우는 한 장의 사진",
                "person": False, "note": "자동 보정",
            })
    return scenes


def build_scene_plan(script_text: str, timing: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    sentences = [s["text"] for s in timing.get("sentences", [])]
    if not sentences:
        raise ClaudeCliError("타이밍 정보가 없어 씬을 나눌 수 없습니다. 먼저 정렬(align) 단계를 완료하세요.")

    prompt = _build_prompt(script_text, sentences, cfg)
    parsed = run_claude_prompt(prompt)

    scenes = parsed.get("scenes") if isinstance(parsed, dict) else None
    if not isinstance(scenes, list) or not scenes:
        raise ClaudeCliError("클로드가 씬 목록을 돌려주지 않았습니다. 다시 시도해주세요.")

    scenes = _validate_and_fix_coverage(scenes, len(sentences))

    # 시간은 timing.json 실측값으로 다시 계산한다.
    for sc in scenes:
        idxs = sorted(sc.get("sentence_indices", []))
        if not idxs:
            sc["start"], sc["end"] = 0.0, 0.0
            continue
        sc["start"] = timing["sentences"][idxs[0]]["start"]
        sc["end"] = timing["sentences"][idxs[-1]]["end"]

    scenes.sort(key=lambda s: s["start"])
    for i, sc in enumerate(scenes):
        sc["id"] = i + 1

    return {"scenes": scenes}
