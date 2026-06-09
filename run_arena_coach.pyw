from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"


def show_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "Arena Coach", 0x10)
    except Exception:
        pass


def launch() -> int:
    venv_pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    executable = venv_pythonw if venv_pythonw.exists() else venv_python
    if executable.exists() and Path(sys.executable).resolve() != executable.resolve():
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        try:
            os.chdir(ROOT)
            subprocess.Popen(
                [str(executable), "-m", "arena_coach.gui.app"],
                cwd=str(ROOT),
                env=env,
            )
            return 0
        except Exception as exc:
            show_error(
                "Arena Coach could not launch from the local virtual environment.\n\n"
                f"{exc}\n\n"
                "Run scripts\\setup_windows.ps1 first."
            )
            return 1

    sys.path.insert(0, str(SRC))
    try:
        from arena_coach.gui.app import main as gui_main
    except Exception as exc:  # noqa: BLE001
        show_error(
            "Arena Coach could not start.\n\n"
            f"{exc}\n\n"
            "Run scripts\\setup_windows.ps1 first, then try again."
        )
        return 1
    return int(gui_main())


if __name__ == "__main__":
    raise SystemExit(launch())
