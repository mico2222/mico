"""롱폼 재활용 모드 — 롱폼 자동편집기 폴더가 있는 사람만 쓰는 기능.

config.yaml 의 modes.recycle 이 false 면 이 모듈은 호출되지 않는다.
`modes.recycle_folder` 가 실제 롱폼 자동편집기의 산출물 폴더를 가리켜야 하며,
그 안에 narration.mp3 / timing.json / scene_plan.json / skit, avatar, mg 파일들이
있다고 가정한다. 이 저장소에는 실제 롱폼 폴더 샘플이 없어 파일명 규칙은 문서에
적힌 실측값을 그대로 따랐다 — 실제 폴더 구조가 다르면 config 로 경로를 맞춰야 한다.

세로 적합도 점수(0~1): 씬 종류별 가중치를 시간으로 가중평균한다.
0.55 이상이면 세로 쇼츠로 뜨기에 적합한 구간으로 추천한다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .common import get_logger

logger = get_logger()

KIND_SCORE = {
    "avatar": 1.0,
    "skit": 0.95,
    "story": 0.95,
    "prop": 0.9,
    "motion": 0.25,
    "html": 0.25,
    "broll": 0.15,
}

KIND_MARK = {
    "avatar": "◉", "skit": "◉", "story": "◉", "prop": "◉",
    "motion": "△", "html": "△", "broll": "△",
}

DEFAULT_MODE_FOR_KIND = {
    "avatar": "fill", "skit": "fill", "story": "fill", "prop": "fill",
    "motion": "full", "html": "full",
}


def _find_first(paths: List[Path]) -> Optional[Path]:
    for p in paths:
        if p.exists():
            return p
    return None


def resolve_scene_media_path(recycle_folder: Path, scene: Dict[str, Any]) -> Optional[Path]:
    """씬 종류별 실측 파일명 규칙으로 재료 경로를 찾는다."""
    kind = scene.get("kind", "")
    sid = scene.get("id")

    if kind == "avatar":
        return _find_first([
            recycle_folder / f"avatar_{sid}_muted.mp4",
            recycle_folder / f"avatar_{sid}.mp4",
        ])
    if kind in ("skit", "story", "prop"):
        return _find_first([
            recycle_folder / "skit" / f"skit_{sid}.mov",
            recycle_folder / "skit" / f"skit_{sid}_loop.mp4",
            recycle_folder / "skit" / f"skit_{sid}.png",
            recycle_folder / "skit" / f"skit_{sid}_full.png",
        ])
    if kind in ("motion", "html"):
        return _find_first([recycle_folder / f"mg_{sid}.mov"])
    if kind == "broll":
        return None  # 일부러 비워두는 자리 — recycle 폴더에는 파일이 없다 (실측)
    return None


def scene_score(scene: Dict[str, Any]) -> float:
    return KIND_SCORE.get(scene.get("kind", ""), 0.5)


def compute_vertical_fitness(scenes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """씬 목록 전체(또는 사용자가 고른 구간)의 세로 적합도를 계산한다."""
    total_time = sum(max(0.0, s.get("end", 0) - s.get("start", 0)) for s in scenes) or 1.0
    weighted = sum(scene_score(s) * max(0.0, s.get("end", 0) - s.get("start", 0)) for s in scenes)
    score = weighted / total_time

    marked_sentences = []
    for s in scenes:
        mark = KIND_MARK.get(s.get("kind", ""), "△")
        marked_sentences.append(f"{mark} [{s.get('kind','?')}] {s.get('note', '')}")

    return {
        "score": round(score, 3),
        "recommended": score >= 0.55,
        "marks": marked_sentences,
        "kind_counts": _count_kinds(scenes),
    }


def _count_kinds(scenes: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for s in scenes:
        k = s.get("kind", "?")
        counts[k] = counts.get(k, 0) + 1
    return counts


def recommend_segments(scenes: List[Dict[str, Any]], window_seconds: float = 55.0) -> List[Dict[str, Any]]:
    """AI 없이도 항상 결과를 내는 기계적 구간 추천 — 세로 적합도가 높은 연속 구간을 고른다."""
    if not scenes:
        return []
    scenes = sorted(scenes, key=lambda s: s.get("start", 0))
    best: List[Dict[str, Any]] = []
    n = len(scenes)
    for i in range(n):
        acc_time = 0.0
        j = i
        window: List[Dict[str, Any]] = []
        while j < n and acc_time < window_seconds:
            dur = max(0.0, scenes[j].get("end", 0) - scenes[j].get("start", 0))
            window.append(scenes[j])
            acc_time += dur
            j += 1
        if not window:
            continue
        fitness = compute_vertical_fitness(window)
        best.append({
            "start": window[0].get("start"), "end": window[-1].get("end"),
            "score": fitness["score"], "scene_ids": [s["id"] for s in window],
        })
    best.sort(key=lambda b: b["score"], reverse=True)
    return best[:5]


def load_recycle_source(recycle_folder: str) -> Dict[str, Any]:
    """롱폼 폴더에서 narration/timing/scene_plan 을 읽어온다."""
    import json

    folder = Path(recycle_folder)
    if not folder.exists():
        raise RuntimeError(f"롱폼 폴더를 찾을 수 없습니다: {recycle_folder}")

    narration = folder / "narration.mp3"
    timing_path = folder / "timing.json"
    plan_path = folder / "scene_plan.json"
    missing = [p.name for p in (narration, timing_path, plan_path) if not p.exists()]
    if missing:
        raise RuntimeError(f"롱폼 폴더에 다음 파일이 없습니다: {', '.join(missing)}")

    with open(timing_path, "r", encoding="utf-8") as f:
        timing = json.load(f)
    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    return {"narration": str(narration), "timing": timing, "plan": plan, "folder": folder}
