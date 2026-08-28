"""실행.bat 이 부르는 진입점. 처음 켤 때 필요한 최소 패키지를 설치하고 웹 화면을 띄운다."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

BOOTSTRAP_MODULES = {
    "fastapi": "fastapi", "uvicorn": "uvicorn", "yaml": "pyyaml", "multipart": "python-multipart",
}


def ensure_bootstrap_packages() -> None:
    missing = []
    for mod, pip_name in BOOTSTRAP_MODULES.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)

    if not missing:
        return

    print("처음 실행이라 화면을 띄우는 데 필요한 프로그램을 설치합니다. 잠시만 기다려주세요...")
    proc = subprocess.run([sys.executable, "-m", "pip", "install", *missing])
    if proc.returncode != 0:
        print("")
        print("설치에 실패했습니다. 인터넷 연결을 확인하거나, 이 화면을 캡처해서 알려주세요.")
        input("엔터를 누르면 창이 닫힙니다...")
        sys.exit(1)


def main() -> None:
    ensure_bootstrap_packages()
    from server.main import main as server_main

    print("쇼츠자동화 화면을 준비하고 있습니다. 잠시 후 브라우저가 자동으로 열립니다...")
    server_main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:  # noqa: BLE001
        print("")
        print(f"프로그램을 켜는 중 문제가 발생했습니다: {e}")
        input("엔터를 누르면 창이 닫힙니다...")
