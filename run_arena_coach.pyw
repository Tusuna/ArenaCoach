from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import traceback


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
LAUNCHER_LOG = ROOT / "arena_coach_launcher.log"


def show_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "Arena Coach", 0x10)
    except Exception:
        pass


def write_launcher_log(*lines: str) -> None:
    try:
        LAUNCHER_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def launch() -> int:
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    executable = venv_python
    write_launcher_log(
        "Arena Coach launcher starting",
        f"root={ROOT}",
        f"sys.executable={sys.executable}",
        f"venv_python={venv_python}",
    )
    if executable.exists() and Path(sys.executable).resolve() != executable.resolve():
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        try:
            os.chdir(ROOT)
            completed = subprocess.run(
                [str(executable), "-m", "arena_coach.gui.app"],
                cwd=str(ROOT),
                env=env,
                text=True,
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            write_launcher_log(
                "Arena Coach launcher completed via local virtual environment",
                f"command={executable} -m arena_coach.gui.app",
                f"returncode={completed.returncode}",
                "stdout:",
                completed.stdout or "",
                "stderr:",
                completed.stderr or "",
            )
            if completed.returncode != 0:
                preview = (completed.stderr or completed.stdout or "No error text was captured.").strip()
                if len(preview) > 1500:
                    preview = preview[:1500] + "\n... (truncated)"
                show_error(
                    "Arena Coach could not start from the local virtual environment.\n\n"
                    f"{preview}\n\n"
                    f"Launcher log: {LAUNCHER_LOG}"
                )
            return int(completed.returncode)
        except Exception as exc:
            write_launcher_log(
                "Arena Coach launcher failed before GUI startup",
                f"{exc}",
                traceback.format_exc(),
            )
            show_error(
                "Arena Coach could not launch from the local virtual environment.\n\n"
                f"{exc}\n\n"
                f"Launcher log: {LAUNCHER_LOG}\n\n"
                "Run scripts\\setup_windows.ps1 first."
            )
            return 1

    sys.path.insert(0, str(SRC))
    try:
        from arena_coach.gui.app import main as gui_main
    except Exception as exc:  # noqa: BLE001
        write_launcher_log(
            "Arena Coach direct import failed",
            f"{exc}",
            traceback.format_exc(),
        )
        show_error(
            "Arena Coach could not start.\n\n"
            f"{exc}\n\n"
            f"Launcher log: {LAUNCHER_LOG}\n\n"
            "Run scripts\\setup_windows.ps1 first, then try again."
        )
        return 1
    result = int(gui_main())
    write_launcher_log(
        "Arena Coach direct launch completed",
        f"returncode={result}",
    )
    return result


if __name__ == "__main__":
    raise SystemExit(launch())
