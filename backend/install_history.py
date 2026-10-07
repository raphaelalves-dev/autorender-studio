from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path


EXE_NAME = "AutoRenderPreset.exe"
INTERNAL_DIR = "_internal"
HISTORY_DIR = Path("update/history")
RESTORE_LAUNCHER = Path("update/RESTAURAR_VERSAO_ANTERIOR.bat")


class InstallationHistoryError(RuntimeError):
    pass


def _write_restore_scripts(root: Path, entry: Path, *, select_as_previous: bool) -> None:
    script = entry / "RESTAURAR.bat"
    script.write_text(
        "@echo off\n"
        "setlocal EnableExtensions\n"
        "chcp 65001 >nul\n"
        "for %%I in (\"%~dp0..\\..\\..\") do set \"APP_DIR=%%~fI\"\n"
        "set \"SNAP=%~dp0snapshot\"\n"
        "if not exist \"%SNAP%\\AutoRenderPreset.exe\" exit /b 1\n"
        "if not exist \"%SNAP%\\_internal\\base_library.zip\" exit /b 1\n"
        "for /L %%I in (1,1,30) do (\n"
        "  tasklist /FI \"IMAGENAME eq AutoRenderPreset.exe\" | find /I \"AutoRenderPreset.exe\" >nul\n"
        "  if errorlevel 1 goto :restore\n"
        "  timeout /t 1 /nobreak >nul\n"
        ")\n"
        "echo Feche o AutoRender Studio antes de restaurar.\n"
        "exit /b 1\n"
        ":restore\n"
        "robocopy \"%SNAP%\\_internal\" \"%APP_DIR%\\_internal\" /MIR /R:1 /W:1 >nul\n"
        "if errorlevel 8 exit /b 1\n"
        "copy /Y \"%SNAP%\\AutoRenderPreset.exe\" \"%APP_DIR%\\AutoRenderPreset.exe\" >nul\n"
        "if errorlevel 1 exit /b 1\n"
        "if exist \"%SNAP%\\autorendericon.ico\" copy /Y \"%SNAP%\\autorendericon.ico\" \"%APP_DIR%\\autorendericon.ico\" >nul\n"
        "echo restaurado>\"%~dp0estado.txt\"\n"
        "if not defined AUTORENDER_UPDATE_NO_RESTART start \"\" \"%APP_DIR%\\AutoRenderPreset.exe\"\n"
        "exit /b 0\n",
        encoding="utf-8",
    )
    launcher = root / RESTORE_LAUNCHER
    if select_as_previous or not launcher.exists():
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text(
            "@echo off\n"
            "setlocal\n"
            f"call \"%~dp0history\\{entry.name}\\RESTAURAR.bat\"\n"
            "exit /b %errorlevel%\n",
            encoding="utf-8",
        )


def _snapshot(root: Path, version: str, kind: str, package_name: str = "") -> Path:
    executable = root / EXE_NAME
    internal = root / INTERNAL_DIR
    if not executable.is_file() or not (internal / "base_library.zip").is_file():
        raise InstallationHistoryError("Executavel ou _internal ausente; atualizacao bloqueada para evitar perda da versao anterior.")

    history = root / HISTORY_DIR
    history.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    entry = history / f"{stamp}_{version.replace('.', '_')}"
    snapshot = entry / "snapshot"
    try:
        snapshot.mkdir(parents=True)
        shutil.copy2(executable, snapshot / EXE_NAME)
        shutil.copytree(internal, snapshot / INTERNAL_DIR)
        icon = root / "autorendericon.ico"
        if icon.is_file():
            shutil.copy2(icon, snapshot / icon.name)
        (entry / "manifest.json").write_text(
            json.dumps(
                {
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "version": version,
                    "kind": kind,
                    "package": package_name,
                    "snapshot": "snapshot",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        _write_restore_scripts(root, entry, select_as_previous=(kind == "before_update"))
        return entry
    except Exception as exc:
        shutil.rmtree(entry, ignore_errors=True)
        raise InstallationHistoryError(f"Nao foi possivel criar o historico de instalacao: {exc}") from exc


def ensure_baseline_history(root: str | Path, version: str) -> Path:
    """Registra a primeira instalação com rollback, sem duplicar a mesma versão."""
    app_root = Path(root).resolve()
    history = app_root / HISTORY_DIR
    for manifest in history.glob("*/manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("kind") == "baseline" and data.get("version") == version:
            return manifest.parent
    entry = _snapshot(app_root, version, "baseline")
    return entry


def snapshot_before_update(root: str | Path, version: str, package_name: str) -> Path:
    return _snapshot(Path(root).resolve(), version, "before_update", package_name)
