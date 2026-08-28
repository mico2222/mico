"""단계 실행기 — tts → align → plan → image → 검수 → assemble.

모든 단계는 멱등하다: 산출물이 이미 있으면 건너뛴다(강제 재실행 옵션은 별도).
돈이 드는 단계(image, assemble 의 영상 변환 부분)는 검수 승인 전에는 절대 돌지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from . import align as align_mod
from . import assemble as assemble_mod
from . import image_gen as image_gen_mod
from . import scene_planner as scene_planner_mod
from . import tts as tts_mod
from .common import Job, find_capcut_draft_folder, get_logger, log_exception

logger = get_logger()


def _write_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------
# 1) TTS
# ------------------------------------------------------------------
def run_tts(job: Job, cfg: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    narration_path = job.dir / "narration.mp3"
    if not force and job.status.get("tts") == "done" and narration_path.exists():
        return {"skipped": True, "path": str(narration_path)}

    try:
        result = tts_mod.synth_narration(job.script_text, narration_path, cfg)
    except Exception as e:  # noqa: BLE001
        log_exception("run_tts", e)
        job.status["tts"] = "error"
        job.warnings.append(f"목소리 생성 실패: {e}")
        job.save()
        raise

    job.paths["narration"] = str(narration_path)
    job.status["tts"] = "done"
    if result.get("warning"):
        job.warnings.append(result["warning"])
    job.save()
    return result


# ------------------------------------------------------------------
# 2) 정렬(align)
# ------------------------------------------------------------------
def run_align(job: Job, cfg: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    timing_path = job.dir / "timing.json"
    if not force and job.status.get("align") == "done" and timing_path.exists():
        return {"skipped": True, "path": str(timing_path)}

    narration_path = job.paths.get("narration")
    if not narration_path or not Path(narration_path).exists():
        raise RuntimeError("먼저 목소리(TTS) 단계를 완료해야 합니다.")

    try:
        timing = align_mod.align_narration(narration_path, job.script_text)
    except Exception as e:  # noqa: BLE001
        log_exception("run_align", e)
        job.status["align"] = "error"
        job.warnings.append(f"자막 정렬 실패: {e}")
        job.save()
        raise

    _write_json(timing_path, timing)
    job.paths["timing"] = str(timing_path)
    job.status["align"] = "done"
    for w in timing.get("warnings", []):
        if w not in job.warnings:
            job.warnings.append(w)
    job.save()
    return timing


# ------------------------------------------------------------------
# 3) 씬 분할(plan)
# ------------------------------------------------------------------
def run_plan(job: Job, cfg: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    plan_path = job.dir / "scene_plan.json"
    if not force and job.status.get("plan") == "done" and plan_path.exists():
        return {"skipped": True, "path": str(plan_path)}

    timing_path = job.paths.get("timing")
    if not timing_path or not Path(timing_path).exists():
        raise RuntimeError("먼저 정렬(align) 단계를 완료해야 합니다.")
    timing = _read_json(timing_path)

    try:
        plan = scene_planner_mod.build_scene_plan(job.script_text, timing, cfg)
    except Exception as e:  # noqa: BLE001
        log_exception("run_plan", e)
        job.status["plan"] = "error"
        job.warnings.append(f"씬 분할 실패: {e}")
        job.save()
        raise

    _write_json(plan_path, plan)
    job.paths["scene_plan"] = str(plan_path)
    job.status["plan"] = "done"
    job.save()
    return plan


# ------------------------------------------------------------------
# 4) 씬 이미지 (돈이 나가는 단계 — 검수 화면에서 씬 단위로만 호출)
# ------------------------------------------------------------------
def run_image_for_scene(job: Job, scene_id: int, cfg: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    plan_path = job.paths.get("scene_plan")
    if not plan_path:
        raise RuntimeError("먼저 씬 분할(plan) 단계를 완료해야 합니다.")
    plan = _read_json(plan_path)
    scene = next((s for s in plan["scenes"] if s["id"] == scene_id), None)
    if scene is None:
        raise RuntimeError(f"씬 {scene_id} 을 찾을 수 없습니다.")

    images_dir = job.dir / "images"
    images_dir.mkdir(exist_ok=True)
    out_path = images_dir / f"scene_{scene_id}.png"

    if not force and out_path.exists():
        return {"skipped": True, "path": str(out_path)}

    char_cfg = cfg.get("character", {})
    sheet_path = char_cfg.get("sheet_path") if char_cfg.get("use_face") else None

    try:
        result = image_gen_mod.generate_scene_image(scene, out_path, cfg, sheet_path)
    except Exception as e:  # noqa: BLE001
        log_exception("run_image_for_scene", e)
        job.warnings.append(f"씬 {scene_id} 이미지 생성 실패: {e}")
        job.save()
        raise

    job.cost_won += cfg.get("pricing", {}).get("image_won", 55)
    job.scenes_overrides.setdefault(str(scene_id), {})["path"] = str(out_path)
    job.save()
    return result


def run_images_for_missing_scenes(job: Job, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """빈 씬을 한 번에 채운다. 호출 전 반드시 사용자 승인을 받아야 한다 (금액 안내 후)."""
    plan_path = job.paths.get("scene_plan")
    if not plan_path:
        raise RuntimeError("먼저 씬 분할(plan) 단계를 완료해야 합니다.")
    plan = _read_json(plan_path)
    results = []
    for sc in plan["scenes"]:
        override = job.scenes_overrides.get(str(sc["id"]), {})
        if override.get("path") and Path(override["path"]).exists():
            continue
        try:
            results.append(run_image_for_scene(job, sc["id"], cfg))
        except Exception as e:  # noqa: BLE001
            results.append({"error": str(e), "scene_id": sc["id"]})
    return results


def estimate_missing_scene_cost(job: Job, cfg: Dict[str, Any]) -> Dict[str, Any]:
    plan_path = job.paths.get("scene_plan")
    if not plan_path:
        return {"count": 0, "won": 0}
    plan = _read_json(plan_path)
    missing = 0
    for sc in plan["scenes"]:
        override = job.scenes_overrides.get(str(sc["id"]), {})
        if not (override.get("path") and Path(override["path"]).exists()):
            missing += 1
    unit = cfg.get("pricing", {}).get("image_won", 55)
    return {"count": missing, "won": missing * unit}


# ------------------------------------------------------------------
# 5) 검수 승인
# ------------------------------------------------------------------
def approve_review(job: Job) -> None:
    job.status["review"] = "done"
    job.save()


def is_review_approved(job: Job) -> bool:
    return job.status.get("review") == "done"


# ------------------------------------------------------------------
# 6) 조립 (승인 전에는 절대 호출되면 안 된다)
# ------------------------------------------------------------------
def run_assemble(job: Job, cfg: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    if not is_review_approved(job):
        raise RuntimeError("검수 승인 전에는 초안을 만들 수 없습니다. 검수 화면에서 먼저 승인해주세요.")

    draft_name = job.job_id
    draft_folder = find_capcut_draft_folder()
    if not draft_folder:
        raise RuntimeError(
            "캡컷 초안 폴더를 찾지 못했습니다. 캡컷 → 설정 → 초안 위치 경로를 config.yaml 의 "
            "capcut.draft_folder 에 직접 입력해주세요."
        )

    if not force and job.status.get("assemble") == "done" and job.paths.get("draft"):
        return {"skipped": True, "draft_path": job.paths["draft"]}

    timing = _read_json(job.paths["timing"])
    plan = _read_json(job.paths["scene_plan"])

    scene_media: Dict[int, Dict[str, Any]] = {}
    for sc in plan["scenes"]:
        override = job.scenes_overrides.get(str(sc["id"]), {})
        if override.get("path"):
            scene_media[sc["id"]] = override

    try:
        result = assemble_mod.build_draft(
            job.dir, draft_folder, draft_name, job.paths["narration"], timing,
            plan["scenes"], scene_media, cfg,
        )
    except Exception as e:  # noqa: BLE001
        log_exception("run_assemble", e)
        job.status["assemble"] = "error"
        job.warnings.append(f"초안 조립 실패: {e}")
        job.save()
        raise

    job.paths["draft"] = result["draft_path"]
    job.status["assemble"] = "done"
    job.save()
    return result


# ------------------------------------------------------------------
# 전체 파이프라인 (돈 안 드는 부분만 자동, 이미지/조립은 별도 호출)
# ------------------------------------------------------------------
def run_free_stages(job: Job, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """tts → align → plan 까지는 돈이 들지 않으므로 이어서 실행한다."""
    out = {}
    out["tts"] = run_tts(job, cfg)
    out["align"] = run_align(job, cfg)
    out["plan"] = run_plan(job, cfg)
    return out


def scene_review_cards(job: Job) -> List[Dict[str, Any]]:
    """검수 화면에 뿌릴 씬 카드 목록."""
    plan_path = job.paths.get("scene_plan")
    if not plan_path or not Path(plan_path).exists():
        return []
    plan = _read_json(plan_path)
    cards = []
    for sc in plan["scenes"]:
        override = job.scenes_overrides.get(str(sc["id"]), {})
        path = override.get("path")
        rel_path = None
        if path:
            try:
                rel_path = Path(path).relative_to(job.dir).as_posix()
            except ValueError:
                rel_path = None  # job 폴더 밖의 경로면 미리보기를 만들지 않는다
        cards.append({
            "id": sc["id"],
            "note": sc.get("note", ""),
            "image_prompt": sc.get("image_prompt", ""),
            "person": sc.get("person", False),
            "start": sc.get("start"),
            "end": sc.get("end"),
            "path": path,
            "rel_path": rel_path,
            "mode": override.get("mode"),
            "empty": not bool(path),
        })
    return cards
