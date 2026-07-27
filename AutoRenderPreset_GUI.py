from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from backend.app_info import APP_NAME


def runtime_root() -> Path:
    """Retorna a pasta real onde o app deve trabalhar.

    Em modo PyInstaller onedir, queremos usar a pasta do .exe, não a pasta temporária
    interna do bundle. Isso mantém config, preset, entrada, saída e logs ao lado do .exe.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def write_startup_error(root: Path, text: str) -> Path:
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    error_file = logs / "startup_error.log"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with error_file.open("a", encoding="utf-8") as fh:
        fh.write(f"\n===== {stamp} =====\n")
        fh.write(text)
        fh.write("\n")
    return error_file


def show_startup_error(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, message)
        root.destroy()
    except Exception:
        # Se até o tkinter falhar, o erro já foi gravado no log.
        pass


def hide_console_for_gui() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def prepare_portable_config(root: Path) -> Path:
    """Garante estrutura mínima para rodar como app portátil."""
    os.chdir(root)
    for folder in ["config", "preset", "entrada", "saida", "processados", "erros", "logs", "bin", "update"]:
        (root / folder).mkdir(parents=True, exist_ok=True)

    config_path = root / "config" / "settings.json"

    from backend.config import load_config, save_config

    cfg = load_config(config_path)
    config_changed = False

    if not getattr(cfg, "startup_safety_reset_done", False):
        cfg.start_with_windows = False
        cfg.auto_start_on_launch = False
        cfg.startup_safety_reset_done = True
        config_changed = True

    # Em app empacotado, a pasta raiz deve ser a pasta do .exe.
    # Se o settings veio de outro PC com caminho absoluto, normaliza para portátil.
    if getattr(sys, "frozen", False):
        cfg.project_root = "."
        if Path(cfg.input_dir).is_absolute():
            cfg.input_dir = "entrada"
        if Path(cfg.output_dir).is_absolute():
            cfg.output_dir = "saida"
        if Path(cfg.processed_dir).is_absolute():
            cfg.processed_dir = "processados"
        if Path(cfg.error_dir).is_absolute():
            cfg.error_dir = "erros"
        if Path(cfg.logs_dir).is_absolute():
            cfg.logs_dir = "logs"
        if Path(cfg.history_file).is_absolute():
            cfg.history_file = "logs/render_history.json"
        if Path(cfg.preset_file).is_absolute():
            cfg.preset_file = Path(cfg.preset_file).name
        if Path(getattr(cfg, "pair_preset_file", "")).is_absolute():
            cfg.pair_preset_file = Path(cfg.pair_preset_file).name
        if Path(cfg.preset_dir).is_absolute():
            cfg.preset_dir = "preset"
        if Path(getattr(cfg, "update_dir", "update")).is_absolute():
            cfg.update_dir = "update"
        config_changed = True

    if config_changed:
        save_config(cfg, config_path)

    # Entrada/saída diária podem estar em uma unidade de rede temporariamente
    # indisponível. Isso não deve impedir a janela de abrir para que o operador
    # consiga corrigir o caminho ou aguardar a reconexão.
    for path in [
        cfg.resolve_path(cfg.preset_dir),
        cfg.processed_path,
        cfg.error_path,
        cfg.logs_path,
        cfg.update_path,
    ]:
        path.mkdir(parents=True, exist_ok=True)
    return config_path


def main() -> int:
    root = runtime_root()

    try:
        config_path = prepare_portable_config(root)

        # Permite usar o mesmo .exe para comandos técnicos, por exemplo:
        # AutoRenderPreset.exe --config config\settings.json doctor
        if len(sys.argv) > 1:
            from backend.main import main as cli_main

            return cli_main(sys.argv[1:])

        hide_console_for_gui()

        from frontend.ui import launch_ui

        launch_ui(str(config_path))
        return 0

    except Exception:
        trace = traceback.format_exc()
        error_file = write_startup_error(root, trace)
        show_startup_error(
            f"Falha ao iniciar o {APP_NAME}.\n\n"
            f"O erro foi gravado em:\n{error_file}\n\n"
            "Envie esse arquivo junto com os logs para análise."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
