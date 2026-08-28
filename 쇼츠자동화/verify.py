"""0단계 검증 — 코드를 더 짓기 전에, 지금 컴퓨터에서 뭐가 되고 안 되는지 확인한다.

A. 오늘 당장 되는 것 (구글 계정 없이): 파이썬/ffmpeg/캡컷 초안 폴더/빈 초안 생성
B. 구글 키가 있을 때: TTS 인증, claude -p 응답, (선택, 비용 발생) 이미지 1장

결과는 화면과 검증결과.txt 파일에 둘 다 남긴다.
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RESULT_LINES: list = []


def log(msg: str) -> None:
    print(msg)
    RESULT_LINES.append(msg)


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "[통과]" if ok else "[실패]"
    line = f"{mark} {label}" + (f" — {detail}" if detail else "")
    log(line)
    return ok


def section(title: str) -> None:
    log("")
    log(f"===== {title} =====")


def main() -> int:
    from pipeline.common import (
        check_python_version, find_tool, find_capcut_draft_folder, load_env, load_config,
    )

    log(f"쇼츠자동화 0단계 검증 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    load_env()
    cfg = load_config()

    all_a_ok = True

    section("A. 오늘 당장 되는 것 (구글 계정 없이 확인)")

    ok = check_python_version()
    check(f"파이썬 3.10 이상 (현재: {sys.version.split()[0]})", ok)
    all_a_ok &= ok

    ffmpeg = find_tool("ffmpeg")
    ok = ffmpeg is not None
    check("ffmpeg 실행됨", ok, ffmpeg or "설치 필요")
    all_a_ok &= ok

    ffprobe = find_tool("ffprobe")
    ok = ffprobe is not None
    check("ffprobe 실행됨", ok, ffprobe or "설치 필요")
    all_a_ok &= ok

    draft_folder = find_capcut_draft_folder()
    folder_ok = draft_folder is not None
    check("캡컷 초안 폴더를 찾음", folder_ok, draft_folder or "찾지 못함 — 캡컷 → 설정 → 초안 위치 경로를 config.yaml 의 capcut.draft_folder 에 입력하세요")
    all_a_ok &= folder_ok

    # 폴더를 못 찾았으면 엉뚱한 곳에 만들지 않는다 — 이 항목은 그대로 실패로 남긴다.
    draft_ok = False
    if folder_ok:
        try:
            import pycapcut
            folder = pycapcut.DraftFolder(draft_folder)
            script = folder.create_draft("검증용_빈초안", 1080, 1920, 30, allow_replace=True)
            script.save()
            draft_ok = True
            check("빈 1080x1920 초안 생성", True, f"{draft_folder}/검증용_빈초안")
        except Exception as e:  # noqa: BLE001
            check("빈 1080x1920 초안 생성", False, f"{e}")
    else:
        check("빈 1080x1920 초안 생성", False, "초안 폴더를 못 찾아 건너뜀 (폴더 항목이 실패면 이 항목도 항상 실패로 남긴다)")
    all_a_ok &= draft_ok

    log("")
    if all_a_ok:
        log(">> A 단계 전부 통과. 3단계(최소 동작본)로 진행할 수 있습니다.")
    else:
        log(">> A 단계에 실패한 항목이 있습니다. 이걸 먼저 해결해야 다음 단계가 의미가 있습니다.")

    section("B. 구글 키가 생긴 다음에 확인하는 것")

    import os
    google_key = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not google_key:
        log("[건너뜀] env.txt 에 GOOGLE_APPLICATION_CREDENTIALS 가 없습니다. B는 나중에 다시 실행해도 됩니다.")
    else:
        key_ok = Path(google_key).exists()
        check(f"키 파일 경로 존재 ({google_key})", key_ok)

        tts_ok = False
        if key_ok:
            try:
                from pipeline.tts import synth_narration
                out = Path(__file__).resolve().parent / "jobs" / "_verify_tts.mp3"
                out.parent.mkdir(exist_ok=True)
                result = synth_narration("검증용 문장입니다.", out, cfg)
                tts_ok = result.get("engine") == "google"
                check("구글 TTS 한 문장 mp3 생성", tts_ok, result.get("warning") or str(out))
            except Exception as e:  # noqa: BLE001
                check("구글 TTS 한 문장 mp3 생성", False, str(e))

        try:
            from pipeline.claude_cli import quick_check
            r = quick_check()
            check("claude -p '1+1' 응답", r["ok"], r["message"])
        except Exception as e:  # noqa: BLE001
            check("claude -p '1+1' 응답", False, str(e))

        log("")
        log("이미지 생성 검증은 선택 사항입니다. 이 한 장에서 약 55원이 실제로 나갑니다.")
        try:
            answer = input("이미지 1장을 생성해 확인해볼까요? (y/N): ").strip().lower()
        except EOFError:
            answer = "n"
        if answer == "y":
            try:
                from pipeline.image_gen import generate_scene_image
                scene = {"image_prompt": "화면을 가득 채우는 한 장의 사진, 밝은 사무실 책상 위 노트북", "person": False}
                out = Path(__file__).resolve().parent / "jobs" / "_verify_image.png"
                out.parent.mkdir(exist_ok=True)
                result = generate_scene_image(scene, out, cfg)
                check("이미지 1장 생성 (약 55원 사용)", True, str(out))
            except Exception as e:  # noqa: BLE001
                check("이미지 1장 생성", False, str(e))
        else:
            log("[건너뜀] 이미지 생성 검증을 건너뛰었습니다.")

    result_path = Path(__file__).resolve().parent / "검증결과.txt"
    result_path.write_text("\n".join(RESULT_LINES), encoding="utf-8")
    log("")
    log(f"검증 결과를 저장했습니다: {result_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("검증 중 예상치 못한 오류가 발생했습니다:")
        traceback.print_exc()
        sys.exit(1)
    finally:
        try:
            input("\n엔터를 누르면 창이 닫힙니다...")
        except EOFError:
            pass
