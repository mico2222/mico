"""로컬 웹 화면 서버. 더블클릭(실행.bat)으로 켜지고 브라우저가 자동으로 열린다."""
from __future__ import annotations

import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from fastapi import FastAPI, HTTPException, UploadFile, File, Form  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse  # noqa: E402

from pipeline import runner  # noqa: E402
from pipeline.common import (  # noqa: E402
    APP_DIR, CONFIG_PATH, Job, find_capcut_draft_folder, find_tool,
    get_logger, load_config, load_env, validate_script, check_python_version,
)

logger = get_logger()
load_env()
app = FastAPI(title="쇼츠자동화")

STATIC_DIR = Path(__file__).resolve().parent / "static"
NO_STORE = {"Cache-Control": "no-store"}


def _ok(data: Any = None) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, headers=NO_STORE)


def _fail(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message}, status_code=status, headers=NO_STORE)


@app.get("/", response_class=HTMLResponse)
def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers=NO_STORE)


@app.get("/app.js")
def app_js():
    return FileResponse(STATIC_DIR / "app.js", media_type="application/javascript", headers=NO_STORE)


# ------------------------------------------------------------------
# 설치/검증 상태
# ------------------------------------------------------------------
def _check_python_packages() -> Dict[str, bool]:
    packages = {
        "pycapcut": "pycapcut", "fastapi": "fastapi", "uvicorn": "uvicorn",
        "faster_whisper": "faster_whisper", "google_tts": "google.cloud.texttospeech",
        "google_genai": "google.genai", "yaml": "yaml", "dotenv": "dotenv",
        "srt": "srt", "pillow": "PIL",
    }
    import importlib
    result = {}
    for label, mod in packages.items():
        try:
            importlib.import_module(mod)
            result[label] = True
        except Exception:
            result[label] = False
    return result


@app.get("/api/setup-status")
def setup_status():
    packages = _check_python_packages()
    draft_folder = find_capcut_draft_folder()
    import os
    google_key = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    data = {
        "python_ok": check_python_version(),
        "python_version": sys.version.split()[0],
        "ffmpeg": find_tool("ffmpeg") is not None,
        "ffprobe": find_tool("ffprobe") is not None,
        "claude_cli": find_tool("claude") is not None or find_tool("claude.cmd") is not None,
        "capcut_draft_folder": draft_folder,
        "google_key_path": google_key,
        "google_key_exists": bool(google_key) and Path(google_key).exists(),
        "packages": packages,
        "config_file_mtime": CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else None,
    }
    return _ok(data)


@app.post("/api/install-tools")
def install_tools():
    """파이썬 패키지를 설치한다. pip 실행 결과를 한국어로 정리해 돌려준다."""
    import subprocess

    req_path = APP_DIR / "requirements.txt"
    if not req_path.exists():
        return _fail("requirements.txt 를 찾을 수 없습니다.")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_path)],
            capture_output=True, timeout=900, text=True,
        )
    except Exception as e:  # noqa: BLE001
        return _fail(f"설치 중 오류가 발생했습니다: {e}")

    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-10:]
        return _fail("일부 패키지 설치에 실패했습니다:\n" + "\n".join(tail))
    return _ok({"log": proc.stdout[-2000:]})


# ------------------------------------------------------------------
# 설정
# ------------------------------------------------------------------
@app.get("/api/config")
def get_config():
    # config.yaml 은 사람이 직접 텍스트 편집기로 고치는 파일이다 (주석이 담겨 있다).
    # 여기서는 읽기만 한다 — 덮어쓰면 설명 주석이 전부 사라진다.
    return _ok(load_config())


# ------------------------------------------------------------------
# 대본 검사
# ------------------------------------------------------------------
@app.post("/api/validate-script")
def validate(payload: Dict[str, str]):
    text = payload.get("text", "")
    return _ok(validate_script(text, load_config()))


# ------------------------------------------------------------------
# 작업(job)
# ------------------------------------------------------------------
@app.get("/api/jobs")
def list_jobs():
    return _ok(Job.list_all())


@app.post("/api/jobs")
def create_job(payload: Dict[str, str]):
    text = payload.get("text", "")
    name_hint = payload.get("name", "")
    cfg = load_config()
    v = validate_script(text, cfg)
    if not v["ok"]:
        return _fail("대본이 쇼츠 기준을 넘었습니다:\n" + "\n".join(v["errors"]))

    job = Job.create(text, name_hint)
    try:
        runner.run_free_stages(job, cfg)
    except Exception as e:  # noqa: BLE001
        logger.exception("작업 생성 중 실패")
        return _fail(f"작업 준비 중 문제가 발생했습니다: {e}")

    return _ok({"job_id": job.job_id, "warnings": job.warnings, "validation": v})


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str):
    try:
        job = Job.load(job_id)
    except FileNotFoundError:
        return _fail("작업을 찾을 수 없습니다.", 404)
    return _ok({
        "job_id": job.job_id, "status": job.status, "warnings": job.warnings,
        "cost_won": job.cost_won, "paths": job.paths,
    })


@app.get("/api/jobs/{job_id}/review")
def job_review(job_id: str):
    job = Job.load(job_id)
    cards = runner.scene_review_cards(job)
    missing_cost = runner.estimate_missing_scene_cost(job, load_config())
    return _ok({"scenes": cards, "missing_cost": missing_cost, "approved": runner.is_review_approved(job)})


@app.post("/api/jobs/{job_id}/scenes/{scene_id}/image")
def make_scene_image(job_id: str, scene_id: int, force: bool = False):
    job = Job.load(job_id)
    cfg = load_config()
    try:
        result = runner.run_image_for_scene(job, scene_id, cfg, force=force)
    except Exception as e:  # noqa: BLE001
        return _fail(str(e))
    return _ok(result)


@app.get("/api/jobs/{job_id}/scenes/missing-cost")
def missing_cost(job_id: str):
    job = Job.load(job_id)
    return _ok(runner.estimate_missing_scene_cost(job, load_config()))


@app.post("/api/jobs/{job_id}/scenes/fill-missing")
def fill_missing(job_id: str):
    job = Job.load(job_id)
    cfg = load_config()
    results = runner.run_images_for_missing_scenes(job, cfg)
    return _ok(results)


@app.post("/api/jobs/{job_id}/scenes/{scene_id}/media")
async def set_scene_media(
    job_id: str, scene_id: int,
    mode: str = Form("fill"), aspect: float = Form(0.5625),
    pos_x: float = Form(0.5), pos_y: float = Form(0.5),
    file: Optional[UploadFile] = File(None),
):
    job = Job.load(job_id)
    override = job.scenes_overrides.setdefault(str(scene_id), {})
    if file is not None:
        uploads_dir = job.dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        dest = uploads_dir / file.filename
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
        override["path"] = str(dest)
    override["mode"] = mode
    override["aspect"] = aspect
    override["pos_x"] = pos_x
    override["pos_y"] = pos_y
    job.save()
    return _ok(override)


@app.post("/api/jobs/{job_id}/scenes/{scene_id}/nudge")
def nudge_scene(job_id: str, scene_id: int, payload: Dict[str, Any]):
    from pipeline.user_media import nudge_position, crop_for_mode
    from pipeline.common import run_ffprobe

    job = Job.load(job_id)
    override = job.scenes_overrides.get(str(scene_id))
    if not override or not override.get("path"):
        return _fail("먼저 이 씬에 이미지나 영상을 넣어주세요.")

    cfg = load_config()
    placement_cfg = cfg.get("placement", {})
    fine = payload.get("fine", False)
    axis = payload.get("axis", "x")
    direction = 1 if payload.get("direction", 1) > 0 else -1
    percent = placement_cfg.get("nudge_fine_pct", 0.01) if fine else placement_cfg.get("nudge_normal_pct", 0.05)

    mode = override.get("mode", placement_cfg.get("default_mode", "fill"))
    aspect = override.get("aspect", placement_cfg.get("default_aspect", 0.5625))
    pos_x = override.get("pos_x", 0.5)
    pos_y = override.get("pos_y", 0.5)

    try:
        info = run_ffprobe(override["path"])
        src_w, src_h = info["width"], info["height"]
    except Exception:
        src_w, src_h = 1920, 1080

    box, pct = crop_for_mode(src_w, src_h, mode, aspect, pos_x, pos_y)
    crop_w_frac = box.x1 - box.x0
    crop_h_frac = box.y1 - box.y0

    if axis == "x":
        pos_x = nudge_position(pos_x, crop_w_frac, direction, percent)
    else:
        pos_y = nudge_position(pos_y, crop_h_frac, direction, percent)

    override["pos_x"], override["pos_y"] = pos_x, pos_y
    job.save()

    box, pct = crop_for_mode(src_w, src_h, mode, aspect, pos_x, pos_y)
    return _ok({"pos_x": pos_x, "pos_y": pos_y, "occupied_pct": round(pct, 1)})


@app.post("/api/jobs/{job_id}/scenes/{scene_id}/center")
def center_scene(job_id: str, scene_id: int):
    job = Job.load(job_id)
    override = job.scenes_overrides.setdefault(str(scene_id), {})
    override["pos_x"], override["pos_y"] = 0.5, 0.5
    job.save()
    return _ok(override)


@app.post("/api/jobs/{job_id}/center-all")
def center_all(job_id: str):
    job = Job.load(job_id)
    for sid, override in job.scenes_overrides.items():
        override["pos_x"], override["pos_y"] = 0.5, 0.5
    job.save()
    return _ok({"count": len(job.scenes_overrides)})


@app.post("/api/jobs/{job_id}/approve")
def approve(job_id: str):
    job = Job.load(job_id)
    runner.approve_review(job)
    return _ok({"approved": True})


@app.post("/api/jobs/{job_id}/assemble")
def assemble(job_id: str, force: bool = False):
    job = Job.load(job_id)
    cfg = load_config()
    try:
        result = runner.run_assemble(job, cfg, force=force)
    except Exception as e:  # noqa: BLE001
        return _fail(str(e))
    return _ok(result)


@app.post("/api/jobs/{job_id}/rework/{stage}")
def rework(job_id: str, stage: str):
    job = Job.load(job_id)
    cfg = load_config()
    try:
        if stage == "tts":
            result = runner.run_tts(job, cfg, force=True)
        elif stage == "align":
            result = runner.run_align(job, cfg, force=True)
        elif stage == "plan":
            result = runner.run_plan(job, cfg, force=True)
        elif stage == "assemble":
            result = runner.run_assemble(job, cfg, force=True)
        else:
            return _fail(f"알 수 없는 단계입니다: {stage}")
    except Exception as e:  # noqa: BLE001
        return _fail(str(e))
    return _ok(result)


@app.get("/media/{job_id}/{path:path}")
def media(job_id: str, path: str):
    from pipeline.common import JOBS_DIR

    p = JOBS_DIR / job_id / path
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), headers=NO_STORE)


def _open_browser(url: str) -> None:
    time.sleep(1.0)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    import uvicorn

    cfg = load_config()
    host = cfg.get("server", {}).get("host", "127.0.0.1")
    port = cfg.get("server", {}).get("port", 8765)
    url = f"http://{host}:{port}"
    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
