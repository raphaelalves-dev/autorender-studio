from __future__ import annotations

import sys
from pathlib import Path

from backend.app_info import APP_NAME


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AutoRenderStudio"


def _startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'

    launcher = Path(__file__).resolve().parent.parent / "AutoRenderPreset_GUI.py"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    executable = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{executable.resolve()}" "{launcher.resolve()}"'


def configure_start_with_windows(enabled: bool) -> tuple[bool, str]:
    if not sys.platform.startswith("win"):
        return False, "Inicializacao com Windows disponivel apenas no Windows."

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _startup_command())
                return True, f"{APP_NAME} configurado para iniciar com o Windows."
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
            return True, f"{APP_NAME} removido da inicializacao do Windows."
    except Exception as exc:
        return False, f"Nao foi possivel configurar inicializacao com Windows: {exc}"
