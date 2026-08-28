"""씬별 9:16 이미지 생성 (google-genai, 지연 임포트).

돈이 실제로 나가는 지점이다 (장당 약 55원). 이 함수는 호출된 즉시 이미지를
만든다 — 검수 화면에서 사용자가 버튼을 눌렀을 때만 이 함수를 호출하도록
runner.py 쪽에서 반드시 게이트를 걸어야 한다 (여기서는 게이트를 걸지 않는다).
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, Optional

from .common import get_logger

logger = get_logger()

BANNED_WORDS = ["여백", "위아래 빈 공간", "분할 화면", "콜라주"]

STYLE_GUARD = (
    "\n\n[이미지 생성 규칙]\n"
    "- 화면을 가득 채우는 한 장의 사진으로 그려라.\n"
    "- 이미지 안에 글자, 숫자, 로고, 의성어, 말풍선을 절대 넣지 마라.\n"
    "- 사람이 나오면 '진행자'라고만 지칭하고, 나이나 외모(예: 20대 여성)를 절대 언급하지 마라.\n"
    "- 세로 9:16 비율 사진 한 장만 그려라.\n"
)


class ImageGenError(Exception):
    pass


def _sanitize_prompt(prompt: str) -> str:
    out = prompt
    for w in BANNED_WORDS:
        out = out.replace(w, "")
    return out + STYLE_GUARD


def _translate_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "credentials" in msg or "could not determine" in msg:
        return "구글 인증 정보를 찾지 못했습니다. env.txt 의 GOOGLE_APPLICATION_CREDENTIALS 경로를 확인해주세요."
    if "billing" in msg:
        return "구글 클라우드 결제 계정이 연결되지 않았습니다."
    if "permission" in msg or "403" in msg:
        return "이미지 생성 API 사용 권한이 없습니다. Vertex AI 사용 설정을 확인해주세요."
    if "quota" in msg or "429" in msg:
        return "이미지 생성 사용량 한도를 넘었습니다. 잠시 후 다시 시도해주세요."
    return f"이미지 생성 중 문제가 발생했습니다: {str(exc)[:200]}"


def _autocrop_letterbox(img):
    """가장자리 단색 띠(레터박스)를 실측으로 잘라낸다 (가장자리 표준편차 8 미만이면 잘라냄)."""
    import numpy as np

    arr = np.asarray(img.convert("L"), dtype=float)
    h, w = arr.shape
    top, bottom, left, right = 0, h, 0, w

    while top < bottom - 1 and arr[top, :].std() < 8:
        top += 1
    while bottom > top + 1 and arr[bottom - 1, :].std() < 8:
        bottom -= 1
    while left < right - 1 and arr[:, left].std() < 8:
        left += 1
    while right > left + 1 and arr[:, right - 1].std() < 8:
        right -= 1

    if (top, bottom, left, right) == (0, h, 0, w):
        return img
    return img.crop((left, top, right, bottom))


def _force_vertical(img, cfg: Dict[str, Any]):
    """가로로 나온 이미지를 흐림 배경 합성으로 강제 세로화한다."""
    from PIL import Image, ImageFilter

    canvas_w, canvas_h = 1080, 1920
    bg = img.resize((canvas_w, canvas_h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))

    ratio = min(canvas_w / img.width, canvas_h / img.height)
    fg_w, fg_h = int(img.width * ratio), int(img.height * ratio)
    fg = img.resize((fg_w, fg_h), Image.LANCZOS)

    canvas = bg.copy()
    canvas.paste(fg, ((canvas_w - fg_w) // 2, (canvas_h - fg_h) // 2))
    return canvas


def _call_genai(prompt: str, model_name: str, reference_image_path: Optional[str]):
    from google import genai

    client = genai.Client()
    contents = [prompt]
    if reference_image_path and Path(reference_image_path).exists():
        from PIL import Image
        contents.append(Image.open(reference_image_path))

    response = client.models.generate_content(model=model_name, contents=contents)
    for part in response.candidates[0].content.parts:
        if getattr(part, "inline_data", None) is not None:
            from PIL import Image
            return Image.open(io.BytesIO(part.inline_data.data))
    raise ImageGenError("모델이 이미지를 돌려주지 않았습니다.")


def generate_scene_image(
    scene: Dict[str, Any], out_path: Path, cfg: Dict[str, Any], character_sheet_path: Optional[str] = None,
) -> Dict[str, Any]:
    """씬 하나의 9:16 이미지를 만든다. 실패하면 한국어 메시지가 담긴 예외를 던진다."""
    img_cfg = cfg.get("image_gen", {})
    model_name = img_cfg.get("model", "gemini-2.5-flash-image")
    fallback_model = img_cfg.get("fallback_model", "imagen-4.0-generate-001")

    ref_path = character_sheet_path if scene.get("person") and cfg.get("character", {}).get("use_face") else None
    prompt = _sanitize_prompt(scene.get("image_prompt", ""))

    last_exc: Optional[Exception] = None
    last_img = None
    for attempt_model in (model_name, fallback_model):
        try:
            img = _call_genai(prompt, attempt_model, ref_path)
            img = _autocrop_letterbox(img)
            last_img = img
            aspect = img.height / img.width if img.width else 0
            if aspect < 1.55:
                continue  # 다음(상위) 모델로 재시도
            img.save(out_path)
            return {"path": str(out_path), "model": attempt_model, "forced_vertical": False}
        except ImportError as e:
            raise ImageGenError(
                "google-genai 패키지가 설치되지 않았습니다. [도구 설치] 버튼을 눌러 설치해주세요."
            ) from e
        except Exception as e:  # noqa: BLE001
            last_exc = e
            logger.exception("이미지 생성 실패 (모델=%s)", attempt_model)

    # 두 모델 모두 세로 비율이 안 나왔거나 실패한 경우
    if last_img is None:
        if last_exc is not None:
            raise ImageGenError(_translate_error(last_exc))
        raise ImageGenError("이미지를 생성하지 못했습니다.")

    try:
        forced = _force_vertical(last_img, cfg)
        forced.save(out_path)
        return {"path": str(out_path), "model": model_name, "forced_vertical": True}
    except Exception as e:  # noqa: BLE001
        raise ImageGenError(_translate_error(e)) from e
