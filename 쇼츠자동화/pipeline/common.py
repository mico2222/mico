"""공통 유틸리티: 설정/경로/로그/작업(job) 파일/ffprobe/대본 검사.

이 파일은 다른 모든 pipeline 모듈이 의존하는 기반이다. 외부 패키지(yaml 제외)는
쓰지 않는다 — 프로그램이 켜지는 것 자체가 다른 무거운 패키지에 좌우되면 안 된다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# ------------------------------------------------------------------
# 경로
# ------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config.yaml"
ENV_PATH = APP_DIR / "env.txt"
JOBS_DIR = APP_DIR / "jobs"
LOGS_DIR = APP_DIR / "logs"
ASSETS_DIR = APP_DIR / "assets"
PROMPTS_DIR = APP_DIR / "prompts"

JOBS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG: Dict[str, Any] = {
    "channel": {"name": "", "topic": "", "audience": ""},
    "brand": {"primary_color": "#ff1f8f", "accent_color": "#cdf23d"},
    "tts": {"voice_name": "ko-KR-Neural2-C", "speaking_rate": 1.0, "chars_per_sec": 7.3},
    "subtitle": {
        "font_size": 14, "color": "#FFFFFF", "outline_color": "#000000",
        "place_y": -0.72, "safe_top_px": 220, "safe_bottom_px": 320, "safe_right_px": 180,
    },
    "placement": {
        "default_mode": "fill", "default_aspect": 0.5625,
        "default_pos_x": 0.5, "default_pos_y": 0.5,
        "nudge_fine_pct": 0.01, "nudge_normal_pct": 0.05,
    },
    "capcut": {"draft_folder": ""},
    "script_limits": {
        "min_chars": 350, "max_chars": 420, "min_sentences": 8, "max_sentences": 13,
        "min_sentence_len": 15, "max_sentence_len": 40, "hook_max_len": 20, "max_seconds": 60,
    },
    "pricing": {"image_won": 55, "video_won_per_4s": 560},
    "character": {"sheet_path": "assets/character_sheet_916.png", "use_face": False},
    "modes": {"recycle": False, "recycle_folder": "", "allow_horizontal_crop_fallback": False},
    "hook_card": {"enabled": False},
    "image_gen": {"model": "gemini-2.5-flash-image", "fallback_model": "imagen-4.0-generate-001"},
    "server": {"host": "127.0.0.1", "port": 8765},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> Dict[str, Any]:
    """config.yaml 을 읽는다. 없거나 일부 항목이 비어 있어도 기본값으로 채운다."""
    if not CONFIG_PATH.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        user_cfg = yaml.safe_load(f) or {}
    return _deep_merge(json.loads(json.dumps(DEFAULT_CONFIG)), user_cfg)


def load_env() -> Dict[str, str]:
    """env.txt (형식: KEY=VALUE, 한 줄에 하나) 를 읽어 os.environ 에 반영한다."""
    result: Dict[str, str] = {}
    if not ENV_PATH.exists():
        return result
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key:
                os.environ[key] = value
                result[key] = value
    return result


# ------------------------------------------------------------------
# 로그 — 넓은 except 안에서도 반드시 여기로 남긴다 (함정 #11)
# ------------------------------------------------------------------
_logger: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("shorts_auto")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(LOGS_DIR / "app.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(sh)
    _logger = logger
    return logger


def log_exception(context: str, exc: BaseException) -> None:
    get_logger().exception("[%s] 처리 중 오류: %s", context, exc)


# ------------------------------------------------------------------
# 돈 표시
# ------------------------------------------------------------------
def won(amount: float) -> str:
    return f"약 {int(round(amount)):,}원"


# ------------------------------------------------------------------
# 외부 도구 확인 (ffmpeg/ffprobe/claude)
# ------------------------------------------------------------------
def find_tool(name: str) -> Optional[str]:
    """이름으로 실행 파일을 찾는다. 윈도우의 .cmd/.exe 확장자를 모두 시도한다."""
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        for ext in (".cmd", ".exe", ".bat"):
            found = shutil.which(name + ext)
            if found:
                return found
    return None


def tool_available(name: str) -> bool:
    return find_tool(name) is not None


def run_ffprobe(path: str) -> Dict[str, Any]:
    """ffprobe 로 길이(초)·해상도를 읽는다. 실패하면 예외를 던진다."""
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe 를 찾을 수 없습니다. ffmpeg 설치를 확인하세요.")
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_entries", "format=duration:stream=width,height,codec_type",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 실패: {proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    duration = float(data.get("format", {}).get("duration", 0.0))
    width = height = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and width is None:
            width = stream.get("width")
            height = stream.get("height")
    return {"duration": duration, "width": width, "height": height}


def find_capcut_draft_folder() -> Optional[str]:
    """윈도우 기본 위치에서 캡컷 초안 폴더를 찾는다. 못 찾으면 None."""
    cfg = load_config()
    override = cfg.get("capcut", {}).get("draft_folder", "").strip()
    if override and Path(override).exists():
        return override

    candidates: List[Path] = []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft")
        candidates.append(Path(local_appdata) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft")
    home = Path.home()
    candidates.append(home / "AppData" / "Local" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft")
    # macOS
    candidates.append(home / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft")

    for c in candidates:
        if c.exists() and c.is_dir():
            return str(c)
    return None


# ------------------------------------------------------------------
# 작업(job) — 스크립트 하나를 초안으로 만드는 한 번의 처리 단위
# ------------------------------------------------------------------
STAGES = ["tts", "align", "plan", "image", "review", "assemble"]


def slugify(text: str, max_len: int = 20) -> str:
    text = re.sub(r"[^0-9A-Za-z가-힣]+", "_", text).strip("_")
    return text[:max_len] or "job"


def new_job_dir(name_hint: str) -> Path:
    """이미 있으면 _2, _3 을 붙인다 (함정 #10: 같은 초에 여러 작업 생성 충돌 방지)."""
    base = slugify(name_hint)
    candidate = JOBS_DIR / base
    n = 2
    while candidate.exists():
        candidate = JOBS_DIR / f"{base}_{n}"
        n += 1
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


@dataclass
class Job:
    job_id: str
    dir: Path
    script_text: str = ""
    status: Dict[str, str] = field(default_factory=dict)  # stage -> "done"/"pending"/"error"
    paths: Dict[str, str] = field(default_factory=dict)
    cost_won: float = 0.0
    warnings: List[str] = field(default_factory=list)
    scenes_overrides: Dict[str, Any] = field(default_factory=dict)

    @property
    def json_path(self) -> Path:
        return self.dir / "job.json"

    def save(self) -> None:
        data = {
            "job_id": self.job_id,
            "script_text": self.script_text,
            "status": self.status,
            "paths": self.paths,
            "cost_won": self.cost_won,
            "warnings": self.warnings,
            "scenes_overrides": self.scenes_overrides,
        }
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def create(cls, script_text: str, name_hint: str = "") -> "Job":
        job_dir = new_job_dir(name_hint or "shorts")
        job = cls(job_id=job_dir.name, dir=job_dir, script_text=script_text)
        for stage in STAGES:
            job.status[stage] = "pending"
        job.save()
        return job

    @classmethod
    def load(cls, job_id: str) -> "Job":
        job_dir = JOBS_DIR / job_id
        with open(job_dir / "job.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        job = cls(job_id=data["job_id"], dir=job_dir, script_text=data.get("script_text", ""))
        job.status = data.get("status", {})
        job.paths = data.get("paths", {})
        job.cost_won = data.get("cost_won", 0.0)
        job.warnings = data.get("warnings", [])
        job.scenes_overrides = data.get("scenes_overrides", {})
        return job

    @staticmethod
    def list_all() -> List[str]:
        if not JOBS_DIR.exists():
            return []
        return sorted(
            [p.name for p in JOBS_DIR.iterdir() if p.is_dir() and (p / "job.json").exists()],
            reverse=True,
        )


# ------------------------------------------------------------------
# 대본 검사 (반드시 지킬 것 2)
# ------------------------------------------------------------------
_SENT_SPLIT_RE = re.compile(r"[.!?\n]+")
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "]+"
)
_BRACKET_RE = re.compile(r"[()\[\]{}]")
_DIGIT_RE = re.compile(r"\d")


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]


def validate_script(text: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """쇼츠 대본 기준 검사. errors 가 있으면 만들기 전에 반드시 막아야 한다."""
    cfg = cfg or load_config()
    limits = cfg["script_limits"]
    chars_per_sec = cfg["tts"]["chars_per_sec"]

    clean = text.strip()
    char_count = len(re.sub(r"\s+", "", clean))
    sentences = _sentences(clean)
    sentence_count = len(sentences)
    first_len = len(sentences[0]) if sentences else 0
    est_seconds = char_count / chars_per_sec if chars_per_sec else 0.0

    errors: List[str] = []
    warnings: List[str] = []

    if not clean:
        errors.append("대본이 비어 있습니다.")
        return {
            "ok": False, "errors": errors, "warnings": warnings,
            "char_count": 0, "sentence_count": 0, "first_sentence_len": 0, "est_seconds": 0.0,
        }

    if char_count < limits["min_chars"]:
        errors.append(f"대본이 너무 짧습니다 ({char_count}자). {limits['min_chars']}~{limits['max_chars']}자로 맞춰주세요.")
    elif char_count > limits["max_chars"]:
        errors.append(f"대본이 너무 깁니다 ({char_count}자). {limits['min_chars']}~{limits['max_chars']}자로 맞춰주세요.")

    if sentence_count < limits["min_sentences"] or sentence_count > limits["max_sentences"]:
        warnings.append(
            f"문장이 {sentence_count}개입니다. 권장 범위는 {limits['min_sentences']}~{limits['max_sentences']}개입니다."
        )

    for i, s in enumerate(sentences):
        L = len(s)
        if L < limits["min_sentence_len"] or L > limits["max_sentence_len"]:
            warnings.append(f"{i+1}번째 문장 길이가 {L}자입니다 (권장 {limits['min_sentence_len']}~{limits['max_sentence_len']}자).")

    if first_len > limits["hook_max_len"]:
        warnings.append(f"첫 문장(훅)이 {first_len}자입니다. {limits['hook_max_len']}자 이내를 권장합니다.")

    if _EMOJI_RE.search(clean):
        errors.append("대본에 이모지가 있습니다. 목소리로 읽을 수 없으니 지워주세요.")
    if _BRACKET_RE.search(clean):
        errors.append("대본에 괄호( ) [ ] { } 가 있습니다. TTS 지시 태그나 부연설명은 지워주세요.")
    if _DIGIT_RE.search(clean):
        warnings.append("대본에 숫자가 그대로 있습니다. '30%' 대신 '삼십 퍼센트'처럼 읽는 대로 적어주세요.")

    if est_seconds > limits["max_seconds"]:
        warnings.append(f"예상 길이가 {est_seconds:.0f}초로 {limits['max_seconds']}초를 넘습니다.")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "char_count": char_count,
        "sentence_count": sentence_count,
        "sentences": sentences,
        "first_sentence_len": first_len,
        "est_seconds": round(est_seconds, 1),
    }


def check_python_version() -> bool:
    return sys.version_info >= (3, 10)
