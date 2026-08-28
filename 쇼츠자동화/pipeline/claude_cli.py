"""claude -p 호출 — 씬 분할에만 쓰는 유일한 LLM 호출 지점.

반드시 지킬 것:
- 프롬프트는 표준입력(stdin)으로 넣는다 (윈도우 명령줄 8,191자 상한 회피)
- ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN 을 지우고 실행한다 (구독 과금 유지)
- --output-format json 의 result 필드는 순수 JSON 이 아니라 울타리에 싸여 온다.
  raw_decode 로 첫 JSON 값만 뽑는다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Dict, Optional

from .common import get_logger

logger = get_logger()


class ClaudeCliError(Exception):
    """한국어 메시지를 담는 예외. 화면에 그대로 보여주면 된다."""


def _resolve_claude_command() -> list:
    """claude 실행 경로를 찾는다. 윈도우 npm 설치본은 claude.cmd 라서 cmd.exe 로 감싼다."""
    direct = shutil.which("claude")
    if direct and os.name != "nt":
        return [direct]

    if os.name == "nt":
        # npm 전역 설치는 claude.cmd 형태라 cmd.exe /c 로 호출해야 셸 스크립트가 해석된다.
        for candidate in ("claude.cmd", "claude.exe", "claude"):
            found = shutil.which(candidate)
            if found:
                return ["cmd.exe", "/c", found]
        return ["cmd.exe", "/c", "claude"]

    if direct:
        return [direct]
    return ["claude"]


def extract_first_json(text: str) -> Optional[Any]:
    """문자열 어딘가에서 시작하는 첫 JSON 값 하나만 뽑아낸다.

    claude -p 의 result 필드는 세 겹따옴표(```json ... ```) 등으로 감싸져 오는 경우가
    있어 그냥 json.loads 로는 실패한다. 첫 '{' 또는 '[' 를 찾아 raw_decode 로 파싱한다.
    """
    if not text:
        return None
    brace_idx = text.find("{")
    bracket_idx = text.find("[")
    candidates = [i for i in (brace_idx, bracket_idx) if i != -1]
    if not candidates:
        return None
    start = min(candidates)
    decoder = json.JSONDecoder()
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text[start:])
            return obj
        except json.JSONDecodeError:
            next_brace = text.find("{", start + 1)
            next_bracket = text.find("[", start + 1)
            nxt = [i for i in (next_brace, next_bracket) if i != -1]
            start = min(nxt) if nxt else -1
    return None


def _translate_error(stderr: str, returncode: int) -> str:
    low = stderr.lower()
    if "not logged in" in low or "login" in low or "authenticat" in low:
        return "클로드 코드 로그인이 필요합니다. 터미널에서 'claude' 를 실행해 로그인 창을 열어주세요."
    if "rate limit" in low or "429" in low:
        return "클로드 사용량이 잠시 초과됐습니다. 몇 분 뒤 다시 시도해주세요."
    if "credit" in low or "billing" in low:
        return "클로드 사용 한도 또는 결제 문제로 보입니다. 구독 상태를 확인해주세요."
    if not stderr.strip():
        return f"클로드 실행이 실패했습니다 (종료 코드 {returncode})."
    return f"클로드 실행 중 오류가 발생했습니다: {stderr.strip()[:300]}"


def run_claude_prompt(prompt: str, timeout: int = 240) -> Any:
    """claude -p 를 stdin 으로 호출하고, 이중으로 감싸진 JSON을 풀어서 돌려준다."""
    cmd = _resolve_claude_command()
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)

    full_cmd = cmd + ["-p", "--output-format", "json"]
    try:
        proc = subprocess.run(
            full_cmd,
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as e:
        raise ClaudeCliError(
            "클로드 코드(claude) 명령을 찾을 수 없습니다. [도구 설치] 버튼을 눌러 설치해주세요."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ClaudeCliError("클로드가 시간 안에 응답하지 않았습니다. 잠시 후 다시 시도해주세요.") from e

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "ignore")
        logger.error("claude -p 실패 (code=%s): %s", proc.returncode, stderr)
        raise ClaudeCliError(_translate_error(stderr, proc.returncode))

    stdout = proc.stdout.decode("utf-8", "ignore")
    envelope = extract_first_json(stdout)
    if envelope is None:
        logger.error("claude -p 응답에서 JSON 봉투를 찾지 못함: %s", stdout[:500])
        raise ClaudeCliError("클로드 응답을 이해하지 못했습니다 (JSON 형식이 아님).")

    result_text = envelope.get("result") if isinstance(envelope, dict) else None
    if result_text is None:
        logger.error("claude -p 응답에 result 필드가 없음: %s", envelope)
        raise ClaudeCliError("클로드 응답에 결과(result) 내용이 없습니다.")

    inner = extract_first_json(result_text) if isinstance(result_text, str) else result_text
    if inner is None:
        logger.error("claude -p result 안에서 JSON을 찾지 못함: %s", str(result_text)[:500])
        raise ClaudeCliError("클로드가 돌려준 내용에서 JSON을 찾지 못했습니다. 프롬프트를 다시 확인해주세요.")

    return inner


def quick_check() -> Dict[str, Any]:
    """0단계 검증용: claude -p '1+1' 이 답하는지 확인."""
    cmd = _resolve_claude_command()
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    try:
        proc = subprocess.run(
            cmd + ["-p"], input=b"1+1", capture_output=True, timeout=60, env=env
        )
    except FileNotFoundError:
        return {"ok": False, "message": "claude 명령을 찾을 수 없습니다."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "claude 응답이 시간 안에 오지 않았습니다."}
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "ignore")
        return {"ok": False, "message": _translate_error(stderr, proc.returncode)}
    out = proc.stdout.decode("utf-8", "ignore").strip()
    return {"ok": True, "message": out[:200]}
