from __future__ import annotations

import subprocess
import sys
from typing import Any


def hidden_console_kwargs() -> dict[str, Any]:
    """Opcoes para executar ferramentas de console sem piscar uma janela no Windows."""
    if not sys.platform.startswith("win"):
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }
