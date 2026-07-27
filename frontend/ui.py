from __future__ import annotations

import os
import math
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import zipfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from backend.app_info import (
    APP_DEVELOPER,
    APP_ICON_NAME,
    APP_LAST_UPDATE,
    APP_NAME,
    APP_OWNER,
    APP_PUBLISHED_DATE,
    APP_USER_MODEL_ID,
    APP_VERSION,
)
from backend.config import (
    AppConfig,
    DEFAULT_DAILY_INPUT_FORMAT,
    DEFAULT_DAILY_OUTPUT_FORMAT,
    MAX_PARALLEL_WORKERS,
    clamp_parallel_workers,
    effective_render_format,
    ensure_directories,
    load_config,
    save_config,
)
from backend.daily_cleanup import cleanup_processed_before_today, cleanup_quarantine_before_today
from backend.delivery import resume_pending_deliveries
from backend.logger import append_text_line
from backend.updater import UpdatePackageError, prepare_update_zip
from backend.startup import configure_start_with_windows
from backend.watcher import (
    IdleRescanTracker,
    render_all_candidates,
    render_available_candidates,
    render_both_candidates,
    render_manual_82_candidates,
    rescan_pending_input,
)


COLORS = {
    "bg": "#040816",
    "surface": "#071120",
    "card": "#091426",
    "card_2": "#0e1b31",
    "card_border": "#193653",
    "title": "#f2f7ff",
    "text": "#c9d9ec",
    "muted": "#8298b8",
    "line": "#18314d",
    "navy": "#06101f",
    "navy_2": "#10243d",
    "cyan": "#38bdf8",
    "cyan_soft": "#0b2b46",
    "green": "#49f2a9",
    "green_dark": "#25c982",
    "green_soft": "#0b3029",
    "yellow": "#ffc247",
    "yellow_soft": "#352a13",
    "red": "#ff5964",
    "red_dark": "#d83b49",
    "red_soft": "#351823",
    "blue": "#38bdf8",
    "blue_soft": "#0b2b46",
    "log_bg": "#050d1b",
    "log_text": "#c8dcf5",
}

RENDER_FORMAT_LABELS = {
    "single": "1 vídeo (82)",
    "pair": "2 vídeos (81+82)",
    "both": "Ambos",
    "all": "Todos (81 a 86)",
}
RENDER_FORMAT_VALUES = {label: value for value, label in RENDER_FORMAT_LABELS.items()}
WINDOW_MIN_WIDTH = 980
WINDOW_MIN_HEIGHT = 640
_WINDOW_GEOMETRY_PATTERN = re.compile(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$")


def render_format_label(config: AppConfig) -> str:
    effective = effective_render_format(config)
    if effective == "all" and getattr(config, "render_format", "pair") != "all":
        return "Automático: todos (1 preset)"
    return RENDER_FORMAT_LABELS.get(effective, "2 vídeos (81+82)")


def _visual_activity_state(message: str, processing: bool) -> str:
    text = str(message or "").casefold()
    if any(word in text for word in ("erro", "falha", "falhou")):
        return "error"
    if not processing:
        return "idle"
    if any(word in text for word in ("enviando", "entrega", "saída em segundo plano")):
        return "delivery"
    if any(word in text for word in ("aguardando", "nenhum", "monitorando", "auto iniciado")):
        return "monitoring"
    if any(word in text for word in ("ok:", "finalizada", "finalizado", "concluída")):
        return "complete"
    return "rendering"


def _validated_window_geometry(
    value: str,
    *,
    screen_width: int,
    screen_height: int,
    default: str = "1180x760",
) -> str:
    """Valida a geometria salva e mantém parte da janela visível após trocar de monitor."""
    match = _WINDOW_GEOMETRY_PATTERN.fullmatch(str(value or "").strip())
    if not match:
        return default

    width, height, x, y = (int(part) for part in match.groups())
    width = max(WINDOW_MIN_WIDTH, min(width, max(WINDOW_MIN_WIDTH, screen_width * 2)))
    height = max(WINDOW_MIN_HEIGHT, min(height, max(WINDOW_MIN_HEIGHT, screen_height * 2)))
    x = max(-width + 140, min(x, screen_width - 140))
    y = max(0, min(y, screen_height - 70))
    return f"{width}x{height}{x:+d}{y:+d}"


def _calculate_resized_geometry(
    direction: str,
    *,
    delta_x: int,
    delta_y: int,
    x: int,
    y: int,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    new_x, new_y = x, y
    new_width, new_height = width, height
    if "e" in direction:
        new_width = max(WINDOW_MIN_WIDTH, width + delta_x)
    if "s" in direction:
        new_height = max(WINDOW_MIN_HEIGHT, height + delta_y)
    if "w" in direction:
        new_width = max(WINDOW_MIN_WIDTH, width - delta_x)
        new_x = x + width - new_width
    if "n" in direction:
        new_height = max(WINDOW_MIN_HEIGHT, height - delta_y)
        new_y = y + height - new_height
    return new_x, new_y, new_width, new_height


def _server_probe_target(value: str) -> str:
    """Retorna o ponto mais estável para testar um disco ou compartilhamento de saída."""
    raw = os.path.expandvars(str(value or "").strip())
    if not raw:
        return ""
    if re.match(r"^[A-Za-z]:[\\/]", raw):
        return f"{raw[:2]}\\"
    if raw.startswith(("\\\\", "//")):
        parts = [part for part in re.split(r"[\\/]+", raw.lstrip("\\/")) if part]
        if len(parts) >= 2:
            return f"\\\\{parts[0]}\\{parts[1]}\\"
    return raw


def _directory_accessible(value: str) -> bool:
    if not value:
        return False
    try:
        with os.scandir(value) as iterator:
            next(iterator, None)
        return True
    except (OSError, ValueError):
        return False

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020


def _taskbar_extended_style(style: int) -> int:
    """Transforma uma janela auxiliar em uma janela normal da barra de tarefas."""
    return (int(style) & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW


def _set_taskbar_window_style(window: tk.Misc) -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = ctypes.c_bool

        window.update_idletasks()
        tk_hwnd = ctypes.c_void_p(int(window.winfo_id()))
        parent_hwnd = user32.GetParent(tk_hwnd)
        hwnd = ctypes.c_void_p(parent_hwnd) if parent_hwnd else tk_hwnd
        style = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
        taskbar_style = _taskbar_extended_style(style)
        if taskbar_style != style:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, taskbar_style)
        user32.SetWindowPos(
            hwnd,
            None,
            0,
            0,
            0,
            0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        return True
    except Exception:
        return False


def _set_windows_app_user_model_id() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _round_rectangle(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs) -> int:
    radius = max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=18, **kwargs)


class RoundedPanel(tk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        *,
        fill: str,
        outline: str,
        radius: int = 18,
        padding: int = 10,
        height: int | None = None,
    ):
        parent_bg = parent.cget("bg") if hasattr(parent, "cget") else COLORS["bg"]
        super().__init__(parent, bg=parent_bg, highlightthickness=0)
        self._fill = fill
        self._outline = outline
        self._radius = radius
        self._padding = padding
        canvas_options: dict[str, object] = {
            "bg": parent_bg,
            "highlightthickness": 0,
            "bd": 0,
        }
        if height is not None:
            canvas_options["height"] = height
        self._canvas = tk.Canvas(self, **canvas_options)
        self._canvas.pack(fill="both", expand=True)
        self.content = tk.Frame(self._canvas, bg=fill, highlightthickness=0, bd=0)
        self._window_id = self._canvas.create_window(padding, padding, anchor="nw", window=self.content)
        self._canvas.bind("<Configure>", self._redraw)

    def _redraw(self, event) -> None:
        self._canvas.delete("panel")
        _round_rectangle(
            self._canvas,
            1,
            1,
            max(2, event.width - 2),
            max(2, event.height - 2),
            self._radius,
            fill=self._fill,
            outline=self._outline,
            width=1,
            tags="panel",
        )
        self._canvas.tag_lower("panel")
        self._canvas.coords(self._window_id, self._padding, self._padding)
        self._canvas.itemconfigure(
            self._window_id,
            width=max(1, event.width - (self._padding * 2)),
            height=max(1, event.height - (self._padding * 2)),
        )

    def set_outline(self, outline: str) -> None:
        self._outline = outline
        try:
            self._canvas.itemconfigure("panel", outline=outline)
        except tk.TclError:
            pass


class RenderActivityCanvas(tk.Canvas):
    """Ilustração vetorial leve: usa somente formas simples do Tkinter."""

    STATE_COLORS = {
        "idle": "#38bdf8",
        "monitoring": "#ffc247",
        "rendering": "#49f2a9",
        "delivery": "#38bdf8",
        "complete": "#75f5b7",
        "error": "#ff6570",
    }

    STATE_LABELS = {
        "idle": "PRONTO",
        "monitoring": "MONITORANDO",
        "rendering": "RENDERIZANDO",
        "delivery": "ENVIANDO",
        "complete": "CONCLUÍDO",
        "error": "ATENÇÃO",
    }

    def __init__(self, parent: tk.Widget):
        super().__init__(
            parent,
            bg=COLORS["card"],
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
        )
        self._phase = 0.0
        self._processing = False
        self._message = "Sistema pronto"
        self._state = "idle"
        self._after_id: str | None = None
        self._scene_ready = False
        self.bind("<Configure>", self._on_resize)
        self._schedule(80)

    def set_processing(self, active: bool) -> None:
        self._processing = bool(active)
        self._state = _visual_activity_state(self._message, self._processing)
        self._update_text()
        self._schedule(30)

    def set_status(self, message: str) -> None:
        self._message = str(message or "Sistema pronto")
        self._state = _visual_activity_state(self._message, self._processing)
        self._update_text()

    def _on_resize(self, _event=None) -> None:
        self._draw_scene()

    def _draw_scene(self) -> None:
        width = max(320, self.winfo_width())
        height = max(320, self.winfo_height())
        self.delete("all")

        self.create_text(
            24,
            24,
            anchor="nw",
            text="RENDER FLOW",
            fill=COLORS["cyan"],
            font=("Segoe UI", 9, "bold"),
        )
        self._state_text = self.create_text(
            width - 24,
            24,
            anchor="ne",
            text=self.STATE_LABELS[self._state],
            fill=self.STATE_COLORS[self._state],
            font=("Segoe UI", 10, "bold"),
        )

        center_y = max(145, int(height * 0.42))
        left_x1, left_x2 = 24, max(126, int(width * 0.31))
        right_x1, right_x2 = min(width - 126, int(width * 0.69)), width - 24
        card_y1, card_y2 = center_y - 54, center_y + 54

        _round_rectangle(
            self,
            left_x1,
            card_y1,
            left_x2,
            card_y2,
            16,
            fill=COLORS["card_2"],
            outline=COLORS["card_border"],
            width=1,
        )
        _round_rectangle(
            self,
            right_x1,
            card_y1,
            right_x2,
            card_y2,
            16,
            fill=COLORS["card_2"],
            outline=COLORS["card_border"],
            width=1,
        )
        self.create_text(
            (left_x1 + left_x2) / 2,
            center_y - 10,
            text="ENTRADA",
            fill="#d7e9ff",
            font=("Segoe UI", 10, "bold"),
        )
        self.create_text(
            (left_x1 + left_x2) / 2,
            center_y + 15,
            text="MP4",
            fill=COLORS["muted"],
            font=("Consolas", 9),
        )
        self.create_text(
            (right_x1 + right_x2) / 2,
            center_y - 10,
            text="SAÍDA",
            fill=COLORS["green"],
            font=("Segoe UI", 10, "bold"),
        )
        self.create_text(
            (right_x1 + right_x2) / 2,
            center_y + 15,
            text="FINAL",
            fill=COLORS["muted"],
            font=("Consolas", 9),
        )

        processor_x = width / 2
        self.create_line(left_x2 + 10, center_y, right_x1 - 10, center_y, fill=COLORS["card_border"], width=3)
        self.create_oval(
            processor_x - 38,
            center_y - 38,
            processor_x + 38,
            center_y + 38,
            fill="#071426",
            outline="#2e6183",
            width=2,
        )
        self._ring = self.create_arc(
            processor_x - 48,
            center_y - 48,
            processor_x + 48,
            center_y + 48,
            start=0,
            extent=86,
            style="arc",
            outline=self.STATE_COLORS[self._state],
            width=4,
        )
        self.create_text(
            processor_x,
            center_y,
            text="FF",
            fill="#e6f6ff",
            font=("Segoe UI", 15, "bold"),
        )

        self._path_start = left_x2 + 14
        self._path_end = right_x1 - 14
        self._path_y = center_y
        self._flow_dot = self.create_oval(0, 0, 12, 12, fill=self.STATE_COLORS[self._state], outline="")

        bars_top = max(card_y2 + 65, height - 145)
        self._bars = []
        bar_width = max(12, min(22, int((width - 72) / 12)))
        gap = 9
        total_width = (bar_width * 8) + (gap * 7)
        start_x = (width - total_width) / 2
        for index in range(8):
            x1 = start_x + index * (bar_width + gap)
            self._bars.append(
                self.create_rectangle(
                    x1,
                    bars_top + 34,
                    x1 + bar_width,
                    bars_top + 42,
                    fill="#163652",
                    outline="",
                )
            )

        self._message_text = self.create_text(
            width / 2,
            height - 42,
            text=self._short_message(),
            fill=COLORS["muted"],
            font=("Segoe UI", 9),
            width=max(240, width - 48),
        )
        self._scene_ready = True
        self._animate_frame()

    def _short_message(self) -> str:
        compact = " ".join(self._message.split())
        return compact if len(compact) <= 72 else f"{compact[:69]}..."

    def _update_text(self) -> None:
        if not self._scene_ready:
            return
        color = self.STATE_COLORS[self._state]
        self.itemconfigure(self._state_text, text=self.STATE_LABELS[self._state], fill=color)
        self.itemconfigure(self._message_text, text=self._short_message())
        self.itemconfigure(self._ring, outline=color)
        self.itemconfigure(self._flow_dot, fill=color)

    def _schedule(self, delay_ms: int) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self._after_id = self.after(delay_ms, self._animate)

    def _animate(self) -> None:
        self._after_id = None
        if not self.winfo_exists():
            return
        if not self._scene_ready:
            self._draw_scene()
        else:
            self._animate_frame()
        active = self._processing and self._state not in ("error", "complete")
        self._schedule(140 if active else 900)

    def _animate_frame(self) -> None:
        if not self._scene_ready:
            return
        active = self._processing and self._state not in ("error", "complete")
        self._phase = (self._phase + (0.055 if active else 0.018)) % 1.0
        position = self._phase if active else 0.5
        dot_x = self._path_start + ((self._path_end - self._path_start) * position)
        radius = 6 if active else 5
        self.coords(
            self._flow_dot,
            dot_x - radius,
            self._path_y - radius,
            dot_x + radius,
            self._path_y + radius,
        )
        self.itemconfigure(self._ring, start=int(-360 * self._phase))

        base_y = max(self.coords(self._bars[0])[3], 0) if self._bars else 0
        for index, item in enumerate(self._bars):
            coords = self.coords(item)
            if len(coords) < 4:
                continue
            amplitude = 30 if active else 8
            height = 8 + abs(math.sin((self._phase * math.tau) + index * 0.72)) * amplitude
            self.coords(item, coords[0], base_y - height, coords[2], base_y)
            self.itemconfigure(item, fill=self.STATE_COLORS[self._state] if active else "#163652")

    def destroy(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
        super().destroy()


class AutoRenderUI(tk.Tk):
    """Central de produção desktop do AutoRender Studio."""

    def __init__(self, config_path: str = "config/settings.json"):
        _set_windows_app_user_model_id()
        super().__init__()
        self.title(f"{APP_NAME} - Versao {APP_VERSION}")
        self.overrideredirect(True)
        self.config_path = config_path
        self.cfg = self._load_config(config_path)
        self.minsize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.geometry(
            _validated_window_geometry(
                getattr(self.cfg, "window_geometry", ""),
                screen_width=self.winfo_screenwidth(),
                screen_height=self.winfo_screenheight(),
            )
        )
        self._last_update_var = tk.StringVar(value=datetime.now().strftime("%H:%M:%S"))

        self._queue: queue.Queue[str] = queue.Queue()
        self._stop_event = threading.Event()
        self._auto_thread: threading.Thread | None = None
        self._busy_lock = threading.Lock()
        self._gui_log_path = self.cfg.logs_path / "janela_teste.log"
        self._controls_to_lock: list[tuple[tk.Widget, str]] = []
        self._settings_window: tk.Toplevel | None = None
        self._rounded_after_id: str | None = None
        self._geometry_save_after_id: str | None = None
        self._geometry_ready = False
        self._resize_enabled = True
        self._resize_handles: list[tk.Widget] = []
        self._resize_start: tuple[str, int, int, int, int, int, int] | None = None
        self._quiet_log_times: dict[str, datetime] = {}
        self._path_health_queue: queue.Queue[tuple[str, str, bool, bool]] = queue.Queue()
        self._path_health_check_running = False
        self._last_path_health_check = 0.0
        self._run_daily_processed_cleanup()
        pending_deliveries = resume_pending_deliveries(self.cfg)
        if pending_deliveries:
            self._write(f"Retomando {pending_deliveries} entrega(s) pendente(s) para a saída.")

        self._configure_theme()
        self.bind("<Map>", self._restore_borderless)
        self.bind("<Configure>", self._schedule_rounded_window)
        self._build()
        self._install_resize_handles()
        self.bind("<Configure>", self._schedule_geometry_save, add="+")
        self._geometry_ready = True
        self.after(80, self._apply_rounded_window)
        self._apply_window_icon()
        self.after(120, self._apply_taskbar_presence)
        self._configure_startup()
        self._drain_queue()
        self.after(150, self._poll_path_health)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._write("Janela de teste aberta.")
        if getattr(self.cfg, "auto_start_on_launch", False):
            self.after(800, self._start_auto)

    @staticmethod
    def _load_config(config_path: str) -> AppConfig:
        cfg = load_config(config_path)
        if cfg.project_root == ".":
            cfg.project_root = str(Path(config_path).resolve().parent.parent)
        return cfg

    def _run_daily_processed_cleanup(self) -> None:
        try:
            results = (
                cleanup_processed_before_today(self.cfg),
                cleanup_quarantine_before_today(self.cfg),
            )
            if any(result.performed and result.completed for result in results):
                save_config(self.cfg, self.config_path)
            for result in results:
                if result.performed:
                    self._write(result.message)
        except Exception as exc:
            self._write(f"Limpeza diária não executada: {exc}")

    def _configure_theme(self) -> None:
        self.configure(bg=COLORS["bg"])
        self.option_add("*Font", "{Segoe UI} 9")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Card.TFrame", background=COLORS["card"], relief="flat")
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("HeaderTitle.TLabel", background=COLORS["card"], foreground=COLORS["title"], font=("Segoe UI", 22, "bold"))
        style.configure("HeaderSub.TLabel", background=COLORS["card"], foreground=COLORS["cyan"], font=("Consolas", 9, "bold"))
        style.configure("FieldLabel.TLabel", background=COLORS["card"], foreground=COLORS["title"], font=("Segoe UI", 9, "bold"))
        style.configure("Muted.TLabel", background=COLORS["card"], foreground=COLORS["muted"])
        style.configure("Hint.TLabel", background=COLORS["card"], foreground=COLORS["muted"])
        style.configure(
            "TEntry",
            fieldbackground=COLORS["card_2"],
            foreground=COLORS["text"],
            insertcolor=COLORS["text"],
            bordercolor=COLORS["card_border"],
            lightcolor=COLORS["card_border"],
            darkcolor=COLORS["card_border"],
            padding=5,
        )
        style.map("TEntry", fieldbackground=[("disabled", "#0b1628")], foreground=[("disabled", COLORS["muted"])])
        style.configure(
            "TCombobox",
            fieldbackground=COLORS["card_2"],
            background=COLORS["card_2"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["cyan"],
            bordercolor=COLORS["card_border"],
            lightcolor=COLORS["card_border"],
            darkcolor=COLORS["card_border"],
            arrowsize=14,
            padding=5,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", COLORS["card_2"]), ("disabled", "#0b1628")],
            selectbackground=[("readonly", COLORS["card_2"])],
            selectforeground=[("readonly", COLORS["text"])],
            foreground=[("disabled", COLORS["muted"])],
        )
        style.configure("TCheckbutton", background=COLORS["card"], foreground=COLORS["text"])
        style.map(
            "TCheckbutton",
            background=[("active", COLORS["card"])],
            foreground=[("disabled", COLORS["muted"]), ("active", COLORS["cyan"])],
            indicatorcolor=[("selected", COLORS["cyan"]), ("!selected", COLORS["card_2"])],
        )

        button_base = {
            "padding": (13, 7),
            "borderwidth": 1,
            "relief": "flat",
            "font": ("Segoe UI", 9, "bold"),
        }
        style.configure(
            "TButton",
            background=COLORS["card_2"],
            foreground=COLORS["text"],
            bordercolor=COLORS["card_border"],
            lightcolor=COLORS["card_border"],
            darkcolor=COLORS["card_border"],
            **button_base,
        )
        style.map(
            "TButton",
            background=[("active", "#142944"), ("disabled", "#0b1628")],
            foreground=[("active", "#ffffff"), ("disabled", "#536984")],
            bordercolor=[("active", COLORS["cyan"]), ("disabled", COLORS["line"])],
        )
        style.configure(
            "Primary.TButton",
            background=COLORS["cyan"],
            foreground="#03111e",
            bordercolor=COLORS["cyan"],
            lightcolor=COLORS["cyan"],
            darkcolor=COLORS["cyan"],
            **button_base,
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#62cdfa"), ("disabled", "#17364a")],
            foreground=[("disabled", "#65859b")],
            bordercolor=[("disabled", "#17364a")],
        )
        style.configure(
            "Success.TButton",
            background=COLORS["green_dark"],
            foreground="#031a12",
            bordercolor=COLORS["green"],
            lightcolor=COLORS["green"],
            darkcolor=COLORS["green"],
            **button_base,
        )
        style.map("Success.TButton", background=[("active", COLORS["green"]), ("disabled", "#16362f")], foreground=[("disabled", "#638e7e")])
        style.configure("Warning.TButton", background=COLORS["yellow"], foreground="#221704", bordercolor=COLORS["yellow"], **button_base)
        style.map("Warning.TButton", background=[("active", "#ffd56f"), ("disabled", "#3b321f")], foreground=[("disabled", "#82734e")])
        style.configure("Danger.TButton", background=COLORS["red_dark"], foreground="#ffffff", bordercolor=COLORS["red"], **button_base)
        style.map("Danger.TButton", background=[("active", COLORS["red"]), ("disabled", "#381923")], foreground=[("disabled", "#8b5661")])

    def _initialize_vars(self) -> None:
        self.vars = {
            "input_dir": tk.StringVar(value=str(self.cfg.input_path)),
            "output_dir": tk.StringVar(value=str(self.cfg.output_path)),
            "daily_input_enabled": tk.BooleanVar(value=getattr(self.cfg, "daily_input_enabled", False)),
            "daily_input_root": tk.StringVar(value=getattr(self.cfg, "daily_input_root", "")),
            "daily_input_format": tk.StringVar(value=getattr(self.cfg, "daily_input_format", DEFAULT_DAILY_INPUT_FORMAT)),
            "daily_output_enabled": tk.BooleanVar(value=getattr(self.cfg, "daily_output_enabled", False)),
            "daily_output_root": tk.StringVar(value=getattr(self.cfg, "daily_output_root", "")),
            "daily_output_format": tk.StringVar(value=getattr(self.cfg, "daily_output_format", DEFAULT_DAILY_OUTPUT_FORMAT)),
            "logs_dir": tk.StringVar(value=str(self.cfg.logs_path)),
            "pair_preset_file": tk.StringVar(value=str(self.cfg.pair_preset_path)),
            "preset_file": tk.StringVar(value=str(self.cfg.preset_path)),
            "mode": tk.StringVar(value=self.cfg.mode),
            "codec": tk.StringVar(value=self.cfg.export.codec),
            "render_format": tk.StringVar(
                value=RENDER_FORMAT_LABELS.get(getattr(self.cfg, "render_format", "pair"), "2 vídeos (81+82)")
            ),
            "parallel_workers": tk.StringVar(value=str(getattr(self.cfg, "parallel_workers", 1))),
            "start_with_windows": tk.BooleanVar(value=getattr(self.cfg, "start_with_windows", False)),
            "auto_start_on_launch": tk.BooleanVar(value=getattr(self.cfg, "auto_start_on_launch", False)),
            "update_zip": tk.StringVar(value=""),
            "status": tk.StringVar(value="Aguardando"),
            "input_path_status": tk.StringVar(value="VERIFICANDO PASTA..."),
            "output_path_status": tk.StringVar(value="VERIFICANDO SERVIDOR..."),
        }

    def _build(self) -> None:
        self._initialize_vars()
        shell = tk.Frame(self, bg=COLORS["bg"], highlightthickness=0, bd=0)
        shell.pack(fill="both", expand=True)
        self._title_bar(shell)

        root_panel = RoundedPanel(shell, fill=COLORS["surface"], outline=COLORS["card_border"], radius=22, padding=14)
        root_panel.pack(fill="both", expand=True, padx=10, pady=(8, 10))
        root = root_panel.content
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)

        header = tk.Frame(root, bg=COLORS["surface"], bd=0, highlightthickness=0)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)

        brand = tk.Frame(header, bg=COLORS["surface"], bd=0)
        brand.grid(row=0, column=0, rowspan=2, sticky="w")
        tk.Label(
            brand,
            text="AUTOMATION  ·  RENDER  OPERATIONS",
            bg=COLORS["surface"],
            fg=COLORS["cyan"],
            font=("Consolas", 8, "bold"),
            anchor="w",
        ).pack(anchor="w")
        title_line = tk.Frame(brand, bg=COLORS["surface"], bd=0)
        title_line.pack(anchor="w", pady=(1, 0))
        tk.Label(
            title_line,
            text="AutoRender",
            bg=COLORS["surface"],
            fg=COLORS["title"],
            font=("Segoe UI", 23, "bold"),
        ).pack(side="left")
        tk.Label(
            title_line,
            text=" STUDIO",
            bg=COLORS["surface"],
            fg=COLORS["cyan"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left", pady=(11, 0))

        header_controls = tk.Frame(header, bg=COLORS["surface"], bd=0)
        header_controls.grid(row=0, column=1, sticky="e")
        self.settings_btn = ttk.Button(header_controls, text="CONFIGURAÇÕES", command=self._open_settings)
        self.settings_btn.pack(side="left", padx=(0, 8))
        self._track_control(self.settings_btn)
        self.health_badge = tk.Label(
            header_controls,
            text="AUTO READY",
            bg=COLORS["green_soft"],
            fg=COLORS["green"],
            highlightbackground=COLORS["green_dark"],
            highlightthickness=1,
            padx=14,
            pady=7,
            font=("Segoe UI", 8, "bold"),
        )
        self.health_badge.pack(side="left")

        metrics = tk.Frame(header, bg=COLORS["surface"], bd=0)
        metrics.grid(row=1, column=1, sticky="e", pady=(7, 0))

        def metric(label: str, *, value: str | None = None, variable: tk.Variable | None = None, width: int = 11) -> None:
            chip = tk.Frame(
                metrics,
                bg=COLORS["card"],
                highlightbackground=COLORS["card_border"],
                highlightthickness=1,
                padx=10,
                pady=3,
            )
            chip.pack(side="left", padx=(6, 0))
            tk.Label(
                chip,
                text=label,
                bg=COLORS["card"],
                fg=COLORS["muted"],
                font=("Segoe UI", 7, "bold"),
            ).pack(side="left", padx=(0, 6))
            tk.Label(
                chip,
                text=value,
                textvariable=variable,
                width=width,
                anchor="e",
                bg=COLORS["card"],
                fg=COLORS["title"],
                font=("Consolas", 8, "bold"),
            ).pack(side="left")

        metric("VERSÃO", value=APP_VERSION, width=5)
        metric("FORMATO", variable=self.vars["render_format"], width=17)
        metric("RENDERS", variable=self.vars["parallel_workers"], width=2)

        route = tk.Frame(root, bg=COLORS["surface"], bd=0, highlightthickness=0)
        route.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        route.columnconfigure(0, weight=1, uniform="path_cards")
        route.columnconfigure(1, weight=1, uniform="path_cards")

        def path_card(
            column: int,
            *,
            title: str,
            path_variable: tk.StringVar,
            status_variable: tk.StringVar,
            accent: str,
        ) -> tuple[RoundedPanel, tk.Label, tk.Label]:
            panel = RoundedPanel(
                route,
                fill=COLORS["card"],
                outline=COLORS["card_border"],
                radius=16,
                padding=10,
                height=94,
            )
            panel.grid(
                row=0,
                column=column,
                sticky="ew",
                padx=(0, 6) if column == 0 else (6, 0),
            )
            card = panel.content
            card.columnconfigure(1, weight=1)
            tk.Frame(card, bg=accent, width=4, height=48).grid(row=0, column=0, rowspan=3, sticky="ns", padx=(0, 10))
            tk.Label(
                card,
                text=title,
                bg=COLORS["card"],
                fg=accent,
                font=("Segoe UI", 8, "bold"),
                anchor="w",
            ).grid(row=0, column=1, sticky="ew")
            tk.Label(
                card,
                textvariable=path_variable,
                width=1,
                bg=COLORS["card"],
                fg=COLORS["title"],
                font=("Consolas", 8),
                anchor="w",
            ).grid(row=1, column=1, sticky="ew", pady=(3, 3))
            status_line = tk.Frame(card, bg=COLORS["card"], bd=0)
            status_line.grid(row=2, column=1, sticky="ew")
            dot = tk.Label(
                status_line,
                text="●",
                bg=COLORS["card"],
                fg=COLORS["yellow"],
                font=("Segoe UI", 8, "bold"),
            )
            dot.pack(side="left")
            status = tk.Label(
                status_line,
                textvariable=status_variable,
                bg=COLORS["card"],
                fg=COLORS["yellow"],
                font=("Segoe UI", 7, "bold"),
                anchor="w",
            )
            status.pack(side="left", fill="x", expand=True, padx=(5, 0))
            return panel, dot, status

        self.input_path_card, self.input_access_dot, self.input_access_label = path_card(
            0,
            title="PASTA DE ENTRADA",
            path_variable=self.vars["input_dir"],
            status_variable=self.vars["input_path_status"],
            accent=COLORS["cyan"],
        )
        self.output_path_card, self.output_access_dot, self.output_access_label = path_card(
            1,
            title="SAÍDA / SERVIDOR",
            path_variable=self.vars["output_dir"],
            status_variable=self.vars["output_path_status"],
            accent=COLORS["green"],
        )

        actions = tk.Frame(root, bg=COLORS["surface"], bd=0)
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        tk.Label(
            actions,
            text="MODO DE PRODUÇÃO",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left")
        format_combo = ttk.Combobox(
            actions,
            textvariable=self.vars["render_format"],
            values=list(RENDER_FORMAT_VALUES),
            width=20,
            state="readonly",
        )
        format_combo.pack(side="left", padx=(6, 14))
        self._track_control(format_combo, "readonly")
        self.render_btn = ttk.Button(actions, text="RENDERIZAR FILA", command=self._render_once, style="Primary.TButton")
        self.render_btn.pack(side="left", padx=4)
        self._track_control(self.render_btn)
        self.auto_btn = ttk.Button(actions, text="INICIAR AUTO", command=self._start_auto, style="Success.TButton")
        self.auto_btn.pack(side="left", padx=4)
        self._track_control(self.auto_btn)
        self.stop_btn = ttk.Button(actions, text="PARAR", command=self._stop_auto, state="disabled", style="Danger.TButton")
        self.stop_btn.pack(side="left", padx=4)
        self.open_logs_home_btn = ttk.Button(actions, text="ABRIR LOGS", command=self._open_logs)
        self.open_logs_home_btn.pack(side="right", padx=4)
        self._track_control(self.open_logs_home_btn)

        status_bar = tk.Frame(root, bg=COLORS["surface"], bd=0)
        status_bar.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        tk.Label(
            status_bar,
            text="STATUS",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left")
        self.status_pill = tk.Label(
            status_bar,
            textvariable=self.vars["status"],
            bg=COLORS["blue_soft"],
            fg=COLORS["cyan"],
            highlightbackground=COLORS["card_border"],
            highlightthickness=1,
            padx=10,
            pady=5,
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        )
        self.status_pill.pack(side="left", fill="x", expand=True, padx=(8, 0))

        workspace = tk.Frame(root, bg=COLORS["surface"], highlightthickness=0)
        workspace.grid(row=4, column=0, sticky="nsew")
        workspace.columnconfigure(0, weight=3, uniform="home")
        workspace.columnconfigure(1, weight=2, uniform="home")
        workspace.rowconfigure(0, weight=1)

        terminal_panel = RoundedPanel(
            workspace,
            fill=COLORS["log_bg"],
            outline=COLORS["card_border"],
            radius=18,
            padding=12,
        )
        terminal_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        terminal = terminal_panel.content
        terminal.rowconfigure(1, weight=1)
        terminal.columnconfigure(0, weight=1)
        tk.Label(
            terminal,
            text="●  TERMINAL AO VIVO",
            bg=COLORS["log_bg"],
            fg=COLORS["cyan"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.log = scrolledtext.ScrolledText(
            terminal,
            height=20,
            wrap="word",
            bg=COLORS["log_bg"],
            fg=COLORS["log_text"],
            insertbackground=COLORS["log_text"],
            selectbackground="#123455",
            relief="flat",
            borderwidth=0,
            font=("Consolas", 9),
        )
        self.log.tag_configure("info", foreground="#c8dcf5")
        self.log.tag_configure("success", foreground=COLORS["green"])
        self.log.tag_configure("warning", foreground=COLORS["yellow"])
        self.log.tag_configure("error", foreground="#ff8790")
        self.log.tag_configure("muted", foreground=COLORS["muted"])
        self.log.grid(row=1, column=0, sticky="nsew")

        visual_panel = RoundedPanel(
            workspace,
            fill=COLORS["card"],
            outline=COLORS["card_border"],
            radius=18,
            padding=2,
        )
        visual_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        visual_panel.content.rowconfigure(0, weight=1)
        visual_panel.content.columnconfigure(0, weight=1)
        self.render_visual = RenderActivityCanvas(visual_panel.content)
        self.render_visual.grid(row=0, column=0, sticky="nsew")

        footer = tk.Frame(root, bg=COLORS["surface"], bd=0)
        footer.grid(row=5, column=0, sticky="e", pady=(8, 0))
        tk.Label(
            footer,
            text="ÚLTIMA ATUALIZAÇÃO",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Segoe UI", 7, "bold"),
        ).pack(side="left")
        tk.Label(
            footer,
            textvariable=self._last_update_var,
            bg=COLORS["surface"],
            fg=COLORS["cyan"],
            font=("Consolas", 9),
        ).pack(side="left", padx=(7, 12))
        self._info_button(footer).pack(side="left")

    def _open_settings(self) -> None:
        if self._settings_window is not None and self._settings_window.winfo_exists():
            self._settings_window.deiconify()
            self._settings_window.lift()
            self._settings_window.focus_force()
            return

        win = tk.Toplevel(self)
        self._settings_window = win
        win.title(f"Configurações · {APP_NAME}")
        win.geometry("1080x650")
        win.minsize(940, 600)
        win.configure(bg=COLORS["bg"])
        win.transient(self)
        win.protocol("WM_DELETE_WINDOW", self._close_settings)
        for icon in self._icon_candidates():
            if icon.exists():
                try:
                    win.iconbitmap(default=str(icon))
                    break
                except tk.TclError:
                    continue

        panel = RoundedPanel(win, fill=COLORS["card"], outline=COLORS["card_border"], radius=20, padding=16)
        panel.pack(fill="both", expand=True, padx=12, pady=12)
        root = panel.content
        root.columnconfigure(1, weight=1)

        header = ttk.Frame(root, style="Card.TFrame")
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Configurações", style="HeaderTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Caminhos, presets, desempenho, inicialização e atualização",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w")
        ttk.Button(header, text="Fechar", command=self._close_settings).grid(row=0, column=1, rowspan=2, sticky="e")

        row = 1
        self._path_row(root, row, "Entrada atual", "input_dir", is_file=False)
        row += 1
        self._daily_path_row(root, row, "Entrada por dia", "daily_input_enabled", "daily_input_root", "daily_input_format")
        row += 1
        self._path_row(root, row, "Saída atual", "output_dir", is_file=False)
        row += 1
        self._daily_path_row(root, row, "Saída por dia", "daily_output_enabled", "daily_output_root", "daily_output_format")
        row += 1
        self._path_row(root, row, "Logs", "logs_dir", is_file=False)
        row += 1
        self._path_row(root, row, "Preset Auto", "pair_preset_file", is_file=True)
        row += 1
        self._path_row(root, row, "Preset Individual (82 / Todos)", "preset_file", is_file=True)
        row += 1
        self._update_row(root, row)
        row += 1

        options = ttk.Frame(root, style="Card.TFrame")
        options.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(12, 6))
        ttk.Label(options, text="Modo", style="FieldLabel.TLabel").pack(side="left")
        mode_combo = ttk.Combobox(
            options,
            textvariable=self.vars["mode"],
            values=["ultra", "pdv", "balanced", "quality"],
            width=14,
            state="readonly",
        )
        mode_combo.pack(side="left", padx=(6, 16))
        self._track_control(mode_combo, "readonly")
        ttk.Label(options, text="Codec", style="FieldLabel.TLabel").pack(side="left")
        codec_combo = ttk.Combobox(
            options,
            textvariable=self.vars["codec"],
            values=["auto", "h264_nvenc", "h264_amf", "libx264"],
            width=15,
            state="readonly",
        )
        codec_combo.pack(side="left", padx=(6, 16))
        self._track_control(codec_combo, "readonly")
        ttk.Label(options, text="Simultâneos", style="FieldLabel.TLabel").pack(side="left")
        workers_combo = ttk.Combobox(
            options,
            textvariable=self.vars["parallel_workers"],
            values=[str(item) for item in range(1, MAX_PARALLEL_WORKERS + 1)],
            width=5,
            state="readonly",
        )
        workers_combo.pack(side="left", padx=(6, 16))
        self._track_control(workers_combo, "readonly")
        start_check = ttk.Checkbutton(options, text="Iniciar com Windows", variable=self.vars["start_with_windows"])
        start_check.pack(side="left", padx=(0, 12))
        self._track_control(start_check)
        auto_check = ttk.Checkbutton(options, text="Auto ao abrir", variable=self.vars["auto_start_on_launch"])
        auto_check.pack(side="left")
        self._track_control(auto_check)
        row += 1

        footer = ttk.Frame(root, style="Card.TFrame")
        footer.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.save_btn = ttk.Button(footer, text="Salvar configurações", command=self._save_from_ui, style="Primary.TButton")
        self.save_btn.pack(side="left", padx=(0, 6))
        self._track_control(self.save_btn)
        self.open_logs_btn = ttk.Button(footer, text="Abrir logs", command=self._open_logs)
        self.open_logs_btn.pack(side="left", padx=6)
        self._track_control(self.open_logs_btn)
        self.zip_logs_btn = ttk.Button(footer, text="Gerar ZIP dos logs", command=self._zip_logs)
        self.zip_logs_btn.pack(side="left", padx=6)
        self._track_control(self.zip_logs_btn)
        quarantine_btn = ttk.Button(footer, text="Abrir quarentena", command=self._open_quarantine)
        quarantine_btn.pack(side="left", padx=6)
        self._track_control(quarantine_btn)
        ttk.Button(footer, text="Voltar para Home", command=self._close_settings).pack(side="right")

    def _close_settings(self) -> None:
        win = self._settings_window
        self._settings_window = None
        if win is not None and win.winfo_exists():
            win.destroy()

    def _open_quarantine(self) -> None:
        self.cfg.quarantine_path.mkdir(parents=True, exist_ok=True)
        self._open_folder(self.cfg.quarantine_path)
        self._write(f"Pasta de quarentena aberta: {self.cfg.quarantine_path}")

    def _build_legacy(self) -> None:
        shell = tk.Frame(self, bg=COLORS["bg"], highlightthickness=0, bd=0)
        shell.pack(fill="both", expand=True)
        self._title_bar(shell)

        root_panel = RoundedPanel(shell, fill=COLORS["card"], outline=COLORS["card_border"], radius=22, padding=14)
        root_panel.pack(fill="both", expand=True, padx=10, pady=10)
        root = root_panel.content
        root.columnconfigure(1, weight=1)

        self.vars = {
            "input_dir": tk.StringVar(value=str(self.cfg.input_path)),
            "output_dir": tk.StringVar(value=str(self.cfg.output_path)),
            "daily_input_enabled": tk.BooleanVar(value=getattr(self.cfg, "daily_input_enabled", False)),
            "daily_input_root": tk.StringVar(value=getattr(self.cfg, "daily_input_root", "")),
            "daily_input_format": tk.StringVar(value=getattr(self.cfg, "daily_input_format", DEFAULT_DAILY_INPUT_FORMAT)),
            "daily_output_enabled": tk.BooleanVar(value=getattr(self.cfg, "daily_output_enabled", False)),
            "daily_output_root": tk.StringVar(value=getattr(self.cfg, "daily_output_root", "")),
            "daily_output_format": tk.StringVar(value=getattr(self.cfg, "daily_output_format", DEFAULT_DAILY_OUTPUT_FORMAT)),
            "logs_dir": tk.StringVar(value=str(self.cfg.logs_path)),
            "pair_preset_file": tk.StringVar(value=str(self.cfg.pair_preset_path)),
            "preset_file": tk.StringVar(value=str(self.cfg.preset_path)),
            "mode": tk.StringVar(value=self.cfg.mode),
            "codec": tk.StringVar(value=self.cfg.export.codec),
            "render_format": tk.StringVar(
                value=RENDER_FORMAT_LABELS.get(getattr(self.cfg, "render_format", "pair"), "2 vídeos (81+82)")
            ),
            "parallel_workers": tk.StringVar(value=str(getattr(self.cfg, "parallel_workers", 1))),
            "start_with_windows": tk.BooleanVar(value=getattr(self.cfg, "start_with_windows", False)),
            "auto_start_on_launch": tk.BooleanVar(value=getattr(self.cfg, "auto_start_on_launch", False)),
            "update_zip": tk.StringVar(value=""),
            "status": tk.StringVar(value="Aguardando"),
        }

        row = 0
        header = ttk.Frame(root, style="Card.TFrame")
        header.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=APP_NAME, style="HeaderTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text=f"Versao {APP_VERSION} - estavel", style="HeaderSub.TLabel").grid(row=1, column=0, sticky="w")
        self.health_badge = tk.Label(
            header,
            text="AUTO READY",
            bg=COLORS["green_soft"],
            fg=COLORS["green_dark"],
            padx=12,
            pady=5,
            font=("Segoe UI", 9, "bold"),
        )
        self.health_badge.grid(row=0, column=1, rowspan=2, sticky="e")
        row += 1

        self._path_row(root, row, "Entrada atual", "input_dir", is_file=False)
        row += 1
        self._daily_path_row(
            root,
            row,
            "Entrada por dia",
            "daily_input_enabled",
            "daily_input_root",
            "daily_input_format",
        )
        row += 1
        self._path_row(root, row, "Saída atual", "output_dir", is_file=False)
        row += 1
        self._daily_path_row(
            root,
            row,
            "Saída por dia",
            "daily_output_enabled",
            "daily_output_root",
            "daily_output_format",
        )
        row += 1
        self._path_row(root, row, "Logs", "logs_dir", is_file=False)
        row += 1
        self._path_row(root, row, "Preset Auto", "pair_preset_file", is_file=True)
        row += 1
        self._path_row(root, row, "Preset Individual (82 / Todos)", "preset_file", is_file=True)
        row += 1
        self._update_row(root, row)
        row += 1

        opts = ttk.Frame(root, style="Card.TFrame")
        opts.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        ttk.Label(opts, text="Modo", style="FieldLabel.TLabel").pack(side="left")
        mode_combo = ttk.Combobox(
            opts,
            textvariable=self.vars["mode"],
            values=["ultra", "pdv", "balanced", "quality"],
            width=16,
            state="readonly",
        )
        mode_combo.pack(side="left", padx=(6, 18))
        self._track_control(mode_combo, "readonly")
        ttk.Label(opts, text="Codec", style="FieldLabel.TLabel").pack(side="left")
        codec_combo = ttk.Combobox(
            opts,
            textvariable=self.vars["codec"],
            values=["auto", "h264_nvenc", "h264_amf", "libx264"],
            width=16,
            state="readonly",
        )
        codec_combo.pack(side="left", padx=(6, 18))
        self._track_control(codec_combo, "readonly")
        ttk.Label(opts, text="Simultâneos", style="FieldLabel.TLabel").pack(side="left")
        workers_combo = ttk.Combobox(
            opts,
            textvariable=self.vars["parallel_workers"],
            values=[str(item) for item in range(1, MAX_PARALLEL_WORKERS + 1)],
            width=6,
            state="readonly",
        )
        workers_combo.pack(side="left", padx=(6, 18))
        self._track_control(workers_combo, "readonly")
        start_check = ttk.Checkbutton(
            opts,
            text="Iniciar com Windows",
            variable=self.vars["start_with_windows"],
        )
        start_check.pack(side="left", padx=(0, 12))
        self._track_control(start_check)
        auto_open_check = ttk.Checkbutton(
            opts,
            text="Auto ao abrir",
            variable=self.vars["auto_start_on_launch"],
        )
        auto_open_check.pack(side="left", padx=(0, 12))
        self._track_control(auto_open_check)
        self.save_btn = ttk.Button(opts, text="Salvar", command=self._save_from_ui, style="Primary.TButton")
        self.save_btn.pack(side="left", padx=4)
        self._track_control(self.save_btn)
        self.open_logs_btn = ttk.Button(opts, text="Abrir logs", command=self._open_logs)
        self.open_logs_btn.pack(side="left", padx=4)
        self._track_control(self.open_logs_btn)
        self.zip_logs_btn = ttk.Button(opts, text="Gerar ZIP dos logs", command=self._zip_logs)
        self.zip_logs_btn.pack(side="left", padx=4)
        self._track_control(self.zip_logs_btn)
        row += 1

        buttons = ttk.Frame(root, style="Card.TFrame")
        buttons.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        ttk.Label(buttons, text="Formato (1 preset = automático)", style="FieldLabel.TLabel").pack(side="left", padx=(4, 0))
        format_combo = ttk.Combobox(
            buttons,
            textvariable=self.vars["render_format"],
            values=list(RENDER_FORMAT_VALUES),
            width=18,
            state="readonly",
        )
        format_combo.pack(side="left", padx=(6, 14))
        self._track_control(format_combo, "readonly")
        self.render_btn = ttk.Button(buttons, text="Renderizar fila", command=self._render_once, style="Primary.TButton")
        self.render_btn.pack(side="left", padx=4)
        self._track_control(self.render_btn)
        self.auto_btn = ttk.Button(buttons, text="Auto", command=self._start_auto, style="Primary.TButton")
        self.auto_btn.pack(side="left", padx=4)
        self._track_control(self.auto_btn)
        self.stop_btn = ttk.Button(buttons, text="Parar", command=self._stop_auto, state="disabled", style="Danger.TButton")
        self.stop_btn.pack(side="left", padx=4)
        row += 1

        status_bar = ttk.Frame(root, style="Card.TFrame")
        status_bar.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 8))
        ttk.Label(status_bar, text="Status", style="FieldLabel.TLabel").pack(side="left")
        self.status_pill = tk.Label(
            status_bar,
            textvariable=self.vars["status"],
            bg=COLORS["blue_soft"],
            fg=COLORS["blue"],
            padx=10,
            pady=4,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )
        self.status_pill.pack(side="left", fill="x", expand=True, padx=(8, 0))
        row += 1

        info = (
            "Regra atual: se existir qualquer pasta *_AUTO, *_MANUAL_TODAS, Manual ou Manual_Retry na entrada, vídeos soltos ficam ignorados. "
            "O formato pode gerar somente o vídeo 82, o par 81/82 ou ambos na mesma pasta de saída. "
            "Se a raiz diária estiver ativa, Entrada/Saída usam automaticamente a pasta do dia. "
            "O botão Renderizar fila processa os vídeos elegíveis usando a quantidade de renders simultâneos configurada. "
            "O botão Auto fica procurando novos vídeos."
        )
        ttk.Label(root, text=info, wraplength=920, style="Hint.TLabel").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 6)
        )
        row += 1

        self.log = scrolledtext.ScrolledText(
            root,
            height=22,
            wrap="word",
            bg=COLORS["log_bg"],
            fg=COLORS["log_text"],
            insertbackground=COLORS["log_text"],
            selectbackground="#1e3a5f",
            relief="flat",
            borderwidth=0,
            font=("Consolas", 9),
        )
        self.log.tag_configure("info", foreground="#bfdbfe")
        self.log.tag_configure("success", foreground="#86efac")
        self.log.tag_configure("warning", foreground="#fde68a")
        self.log.tag_configure("error", foreground="#fca5a5")
        self.log.tag_configure("muted", foreground="#94a3b8")
        self.log.grid(row=row, column=0, columnspan=3, sticky="nsew")
        root.rowconfigure(row, weight=1)
        row += 1

        footer = ttk.Frame(root, style="Card.TFrame")
        footer.grid(row=row, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Label(footer, text="Ultima atualizacao:", style="Muted.TLabel").pack(side="left")
        ttk.Label(footer, textvariable=self._last_update_var, style="HeaderSub.TLabel").pack(side="left", padx=(4, 12))
        self._info_button(footer).pack(side="left")

    def _title_bar(self, parent: tk.Widget) -> None:
        bar = tk.Frame(parent, bg=COLORS["navy"], height=42, highlightbackground=COLORS["card_border"], highlightthickness=1)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        accent = tk.Frame(bar, bg=COLORS["cyan"], width=4)
        accent.pack(side="left", fill="y")
        title = tk.Label(
            bar,
            text=f"  AUTORENDER  //  CONTROL CENTER     v{APP_VERSION}",
            bg=COLORS["navy"],
            fg=COLORS["title"],
            font=("Consolas", 9, "bold"),
            anchor="w",
        )
        title.pack(side="left", fill="x", expand=True)

        self.minimize_btn = tk.Button(
            bar,
            text="-",
            command=self._minimize_window,
            bg=COLORS["navy"],
            fg=COLORS["muted"],
            activebackground=COLORS["navy_2"],
            activeforeground="#ffffff",
            bd=0,
            width=4,
            font=("Segoe UI", 12, "bold"),
        )
        self.minimize_btn.pack(side="right", fill="y")

        self.close_top_btn = tk.Button(
            bar,
            text="X",
            command=self._close,
            bg=COLORS["navy"],
            fg="#ff9ca4",
            activebackground=COLORS["red_dark"],
            activeforeground="#ffffff",
            bd=0,
            width=4,
            font=("Segoe UI", 10, "bold"),
        )
        self.close_top_btn.pack(side="right", fill="y")
        self._track_control(self.close_top_btn)

        for widget in (bar, accent, title):
            widget.bind("<ButtonPress-1>", self._begin_move)
            widget.bind("<B1-Motion>", self._move_window)
            widget.bind("<ButtonRelease-1>", self._end_window_interaction)

    def _begin_move(self, event) -> None:
        if not self._resize_enabled:
            self._drag_start = None
            return
        self._drag_start = (event.x_root, event.y_root, self.winfo_x(), self.winfo_y())

    def _move_window(self, event) -> None:
        if not self._resize_enabled or not getattr(self, "_drag_start", None):
            return
        start_x, start_y, win_x, win_y = self._drag_start
        self.geometry(f"+{win_x + event.x_root - start_x}+{win_y + event.y_root - start_y}")

    def _install_resize_handles(self) -> None:
        definitions = (
            ("n", "size_ns", {"relx": 0, "rely": 0, "relwidth": 1, "height": 6}),
            ("s", "size_ns", {"relx": 0, "rely": 1, "y": -6, "relwidth": 1, "height": 6}),
            ("w", "size_we", {"relx": 0, "rely": 0, "width": 6, "relheight": 1}),
            ("e", "size_we", {"relx": 1, "x": -6, "rely": 0, "width": 6, "relheight": 1}),
            ("nw", "size_nw_se", {"relx": 0, "rely": 0, "width": 12, "height": 12}),
            ("ne", "size_ne_sw", {"relx": 1, "x": -12, "rely": 0, "width": 12, "height": 12}),
            ("sw", "size_ne_sw", {"relx": 0, "rely": 1, "y": -12, "width": 12, "height": 12}),
            ("se", "size_nw_se", {"relx": 1, "x": -12, "rely": 1, "y": -12, "width": 12, "height": 12}),
        )
        for direction, cursor, placement in definitions:
            handle = tk.Frame(self, bg=COLORS["bg"], bd=0, highlightthickness=0, cursor=cursor)
            handle.place(**placement)
            handle.bind("<ButtonPress-1>", lambda event, value=direction: self._begin_resize(event, value))
            handle.bind("<B1-Motion>", self._perform_resize)
            handle.bind("<ButtonRelease-1>", self._end_window_interaction)
            setattr(handle, "_resize_cursor", cursor)
            handle.lift()
            self._resize_handles.append(handle)

    def _begin_resize(self, event, direction: str) -> None:
        if not self._resize_enabled:
            return
        self.update_idletasks()
        self._resize_start = (
            direction,
            event.x_root,
            event.y_root,
            self.winfo_x(),
            self.winfo_y(),
            self.winfo_width(),
            self.winfo_height(),
        )

    def _perform_resize(self, event) -> None:
        if not self._resize_enabled or self._resize_start is None:
            return
        direction, start_x, start_y, win_x, win_y, win_width, win_height = self._resize_start
        delta_x = event.x_root - start_x
        delta_y = event.y_root - start_y
        new_x, new_y, new_width, new_height = _calculate_resized_geometry(
            direction,
            delta_x=delta_x,
            delta_y=delta_y,
            x=win_x,
            y=win_y,
            width=win_width,
            height=win_height,
        )

        self.geometry(f"{new_width}x{new_height}{new_x:+d}{new_y:+d}")

    def _end_window_interaction(self, _event=None) -> None:
        self._resize_start = None
        self._drag_start = None
        self._schedule_geometry_save()

    def _set_resize_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._resize_enabled and not enabled:
            self._persist_window_geometry()
        self._resize_enabled = enabled
        if not enabled and self._geometry_save_after_id is not None:
            try:
                self.after_cancel(self._geometry_save_after_id)
            except tk.TclError:
                pass
            self._geometry_save_after_id = None
        for handle in self._resize_handles:
            try:
                cursor = getattr(handle, "_resize_cursor", "arrow") if enabled else "arrow"
                handle.configure(cursor=cursor)
            except tk.TclError:
                pass
        win = self._settings_window
        if win is not None and win.winfo_exists():
            win.resizable(enabled, enabled)

    def _schedule_geometry_save(self, _event=None) -> None:
        if not self._geometry_ready or not self._resize_enabled or self.state() != "normal":
            return
        if self._geometry_save_after_id is not None:
            try:
                self.after_cancel(self._geometry_save_after_id)
            except tk.TclError:
                pass
        self._geometry_save_after_id = self.after(700, self._persist_window_geometry)

    def _persist_window_geometry(self) -> None:
        self._geometry_save_after_id = None
        if not self._geometry_ready or not self._resize_enabled or self.state() != "normal":
            return
        self.update_idletasks()
        geometry = (
            f"{self.winfo_width()}x{self.winfo_height()}"
            f"{self.winfo_x():+d}{self.winfo_y():+d}"
        )
        if geometry == getattr(self.cfg, "window_geometry", ""):
            return
        self.cfg.window_geometry = geometry
        save_config(self.cfg, self.config_path)

    def _minimize_window(self) -> None:
        self._persist_window_geometry()
        self.overrideredirect(False)
        self.update_idletasks()
        self._apply_taskbar_presence()
        self.iconify()

    def _restore_borderless(self, _event=None) -> None:
        if self.state() == "normal":
            self.after(10, self._restore_borderless_window)

    def _restore_borderless_window(self) -> None:
        self.overrideredirect(True)
        self._apply_rounded_window()
        self.after(10, self._apply_taskbar_presence)

    def _apply_taskbar_presence(self) -> None:
        _set_taskbar_window_style(self)

    def _schedule_rounded_window(self, _event=None) -> None:
        if not sys.platform.startswith("win"):
            return
        if self._rounded_after_id:
            try:
                self.after_cancel(self._rounded_after_id)
            except tk.TclError:
                pass
        self._rounded_after_id = self.after(40, self._apply_rounded_window)

    def _apply_rounded_window(self) -> None:
        self._rounded_after_id = None
        if not sys.platform.startswith("win"):
            return
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1:
            return
        try:
            import ctypes

            hwnd = self.winfo_id()
            region = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, 24, 24)
            ctypes.windll.user32.SetWindowRgn(hwnd, region, True)
        except Exception:
            pass

    def _path_row(self, parent: ttk.Frame, row: int, label: str, key: str, *, is_file: bool) -> None:
        ttk.Label(parent, text=label, style="FieldLabel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=self.vars[key])
        entry.grid(row=row, column=1, sticky="ew", pady=4, padx=(8, 6))
        self._track_control(entry)
        button = ttk.Button(
            parent,
            text="Selecionar",
            command=lambda k=key, file_mode=is_file: self._select_path(k, file_mode),
        )
        button.grid(row=row, column=2, sticky="e", pady=4)
        self._track_control(button)

    def _daily_path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        enabled_key: str,
        root_key: str,
        format_key: str,
    ) -> None:
        check = ttk.Checkbutton(
            parent,
            text=label,
            variable=self.vars[enabled_key],
            command=self._refresh_daily_path_fields,
        )
        check.grid(row=row, column=0, sticky="w", pady=4)
        self._track_control(check)

        entry = ttk.Entry(parent, textvariable=self.vars[root_key])
        entry.grid(row=row, column=1, sticky="ew", pady=4, padx=(8, 6))
        self._track_control(entry)

        actions = ttk.Frame(parent, style="Card.TFrame")
        actions.grid(row=row, column=2, sticky="e", pady=4)
        ttk.Label(actions, text="Formato", style="FieldLabel.TLabel").pack(side="left")
        fmt = ttk.Entry(actions, textvariable=self.vars[format_key], width=10)
        fmt.pack(side="left", padx=(4, 4))
        self._track_control(fmt)
        button = ttk.Button(actions, text="Raiz", command=lambda key=root_key: self._select_daily_root(key))
        button.pack(side="left")
        self._track_control(button)

    def _update_row(self, parent: ttk.Frame, row: int) -> None:
        ttk.Label(parent, text="Atualizacao ZIP", style="FieldLabel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=self.vars["update_zip"])
        entry.grid(
            row=row, column=1, sticky="ew", pady=4, padx=(8, 6)
        )
        self._track_control(entry)
        actions = ttk.Frame(parent, style="Card.TFrame")
        actions.grid(row=row, column=2, sticky="e", pady=4)
        select_btn = ttk.Button(actions, text="Selecionar", command=self._select_update_zip)
        select_btn.pack(side="left", padx=(0, 4))
        self._track_control(select_btn)
        load_btn = ttk.Button(actions, text="Carregar", command=self._load_update_zip)
        load_btn.pack(side="left")
        self._track_control(load_btn)

    def _poll_path_health(self) -> None:
        latest: tuple[str, str, bool, bool] | None = None
        while True:
            try:
                latest = self._path_health_queue.get_nowait()
            except queue.Empty:
                break

        if latest is not None:
            checked_input, checked_output, input_ok, output_ok = latest
            self._path_health_check_running = False
            current_input = self.vars["input_dir"].get().strip()
            current_output = self.vars["output_dir"].get().strip()
            if checked_input == current_input and checked_output == current_output:
                self._apply_path_health(input_ok=input_ok, output_ok=output_ok)
            else:
                self._last_path_health_check = 0.0

        now = time.monotonic()
        if not self._path_health_check_running and now - self._last_path_health_check >= 5.0:
            input_path = self.vars["input_dir"].get().strip()
            output_path = self.vars["output_dir"].get().strip()
            self._path_health_check_running = True
            self._last_path_health_check = now
            threading.Thread(
                target=self._check_path_health,
                args=(input_path, output_path),
                daemon=True,
                name="autorender-path-health",
            ).start()

        self.after(750, self._poll_path_health)

    def _check_path_health(self, input_path: str, output_path: str) -> None:
        input_ok = _directory_accessible(input_path)
        output_ok = _directory_accessible(_server_probe_target(output_path))
        self._path_health_queue.put((input_path, output_path, input_ok, output_ok))

    def _apply_path_health(self, *, input_ok: bool, output_ok: bool) -> None:
        input_color = COLORS["green"] if input_ok else COLORS["red"]
        output_color = COLORS["green"] if output_ok else COLORS["red"]
        self.vars["input_path_status"].set(
            "PASTA DE ENTRADA ACESSÍVEL" if input_ok else "PASTA DE ENTRADA INDISPONÍVEL"
        )
        self.vars["output_path_status"].set(
            "SERVIDOR DISPONÍVEL"
            if output_ok
            else "SEM ACESSO AO SERVIDOR — CONTATE O SUPORTE"
        )
        self.input_access_dot.configure(fg=input_color)
        self.input_access_label.configure(fg=input_color)
        self.output_access_dot.configure(fg=output_color)
        self.output_access_label.configure(fg=output_color)
        self.input_path_card.set_outline(input_color if not input_ok else COLORS["card_border"])
        self.output_path_card.set_outline(output_color if not output_ok else COLORS["card_border"])

    def _track_control(self, widget: tk.Widget, normal_state: str = "normal") -> None:
        self._controls_to_lock.append((widget, normal_state))

    def _set_processing_state(self, active: bool) -> None:
        self._set_resize_enabled(not active)
        for widget, normal_state in self._controls_to_lock:
            try:
                widget.configure(state="disabled" if active else normal_state)
            except tk.TclError:
                pass
        self.stop_btn.configure(state="normal" if active else "disabled")
        if hasattr(self, "render_visual"):
            self.render_visual.set_processing(active)

    def _info_button(self, parent: tk.Widget) -> tk.Canvas:
        try:
            parent_bg = parent.cget("background")
        except tk.TclError:
            parent_bg = COLORS["surface"]
        if not parent_bg:
            parent_bg = COLORS["surface"]
        canvas = tk.Canvas(parent, width=34, height=34, highlightthickness=0, bg=parent_bg)
        canvas.create_oval(3, 3, 31, 31, outline=COLORS["cyan"], fill=COLORS["cyan_soft"], width=1)
        canvas.create_text(17, 17, text="i", fill=COLORS["cyan"], font=("Segoe UI", 13, "bold"))
        canvas.bind("<Button-1>", lambda _event: self._show_about())
        canvas.bind("<Enter>", lambda _event: canvas.configure(cursor="hand2"))
        return canvas

    def _apply_window_icon(self) -> None:
        for icon in self._icon_candidates():
            if icon.exists():
                try:
                    self.iconbitmap(default=str(icon))
                    return
                except tk.TclError:
                    continue

    def _icon_candidates(self) -> list[Path]:
        candidates = [
            self.cfg.root_path() / APP_ICON_NAME,
            Path(__file__).resolve().parent.parent / APP_ICON_NAME,
        ]
        bundle_dir = getattr(sys, "_MEIPASS", "")
        if bundle_dir:
            candidates.append(Path(bundle_dir) / APP_ICON_NAME)
        return candidates

    def _configure_startup(self) -> None:
        ok, message = configure_start_with_windows(getattr(self.cfg, "start_with_windows", False))
        if ok:
            save_config(self.cfg, self.config_path)
        self._write(message)

    def _select_path(self, key: str, is_file: bool) -> None:
        if is_file:
            value = filedialog.askopenfilename(
                title="Selecionar preset .MOV",
                filetypes=[("Preset MOV", "*.mov"), ("Vídeos", "*.mov *.mp4 *.mkv"), ("Todos", "*.*")],
            )
        else:
            value = filedialog.askdirectory(title="Selecionar pasta")
        if value:
            self.vars[key].set(value)

    def _select_daily_root(self, key: str) -> None:
        value = filedialog.askdirectory(title="Selecionar raiz fixa")
        if value:
            self.vars[key].set(value)
            self._refresh_daily_path_fields()

    def _apply_daily_vars_to_config(self) -> None:
        self.cfg.daily_input_enabled = bool(self.vars["daily_input_enabled"].get())
        self.cfg.daily_input_root = self.vars["daily_input_root"].get().strip()
        self.cfg.daily_input_format = self.vars["daily_input_format"].get().strip() or DEFAULT_DAILY_INPUT_FORMAT
        self.cfg.daily_output_enabled = bool(self.vars["daily_output_enabled"].get())
        self.cfg.daily_output_root = self.vars["daily_output_root"].get().strip()
        self.cfg.daily_output_format = self.vars["daily_output_format"].get().strip() or DEFAULT_DAILY_OUTPUT_FORMAT

    def _refresh_daily_path_fields(self) -> None:
        self._apply_daily_vars_to_config()
        if self.cfg.daily_input_enabled and self.cfg.daily_input_root:
            self.vars["input_dir"].set(str(self.cfg.input_path))
        if self.cfg.daily_output_enabled and self.cfg.daily_output_root:
            self.vars["output_dir"].set(str(self.cfg.output_path))

    def _select_update_zip(self) -> None:
        value = filedialog.askopenfilename(
            title="Selecionar pacote de atualizacao",
            filetypes=[("Pacote de atualizacao", "*.zip"), ("Todos", "*.*")],
        )
        if value:
            self.vars["update_zip"].set(value)

    def _load_update_zip(self) -> None:
        if self._busy_lock.locked() or (self._auto_thread and self._auto_thread.is_alive()):
            messagebox.showwarning("Atualizacao", "Pare o processamento antes de carregar uma atualizacao.")
            return
        cfg = self._config_from_ui()
        zip_path = self.vars["update_zip"].get().strip()
        if not zip_path:
            messagebox.showwarning("Atualizacao", "Selecione um arquivo .zip de atualizacao.")
            return
        try:
            prepared = prepare_update_zip(zip_path, cfg.root_path())
        except UpdatePackageError as exc:
            messagebox.showerror("Atualizacao", str(exc))
            self._write(f"ERRO ao carregar atualizacao: {exc}")
            return
        except Exception as exc:
            messagebox.showerror("Atualizacao", f"Falha inesperada ao carregar atualizacao:\n{exc}")
            self._write(f"ERRO inesperado ao carregar atualizacao: {exc}")
            return

        mb = prepared.total_size_bytes / (1024 * 1024)
        self._write(f"Atualizacao carregada: {prepared.file_count} arquivo(s), {mb:.2f} MB")
        self._write("Fechando para aplicar atualizacao automaticamente.")
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", prepared.apply_script],
                cwd=str(Path(prepared.apply_script).parent),
                shell=False,
            )
        except Exception as exc:
            messagebox.showerror("Atualizacao", f"Atualizacao preparada, mas nao foi possivel iniciar o aplicador:\n{exc}")
            return
        self.after(500, self.destroy)

    def _show_about(self) -> None:
        win = tk.Toplevel(self)
        win.title(f"Sobre {APP_NAME}")
        win.resizable(False, False)
        win.configure(bg="#050a18")
        win.transient(self)
        for icon in self._icon_candidates():
            if icon.exists():
                try:
                    win.iconbitmap(default=str(icon))
                    break
                except tk.TclError:
                    continue

        card = tk.Frame(win, bg="#050a18", highlightbackground="#0f5d8a", highlightthickness=1, padx=18, pady=16)
        card.pack(padx=14, pady=14, fill="both", expand=True)

        def row(text: str, value: str | None = None) -> None:
            line = tk.Frame(card, bg="#050a18")
            line.pack(anchor="w", pady=3)
            tk.Label(line, text=text, bg="#050a18", fg="#b7cce6", font=("Segoe UI", 10)).pack(side="left")
            if value:
                tk.Label(line, text=value, bg="#050a18", fg="#dcecff", font=("Segoe UI", 10, "bold")).pack(side="left")

        tk.Label(card, text=APP_NAME, bg="#050a18", fg="#ffffff", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 6))
        row("Pertence a ", APP_OWNER)
        row("Desenvolvido por ", APP_DEVELOPER)
        row("Versao ", APP_VERSION)
        row("Publicado em ", APP_PUBLISHED_DATE)
        row("Ultima update ", APP_LAST_UPDATE)

        ttk.Button(card, text="Fechar", command=win.destroy).pack(anchor="e", pady=(12, 0))
        win.update_idletasks()
        x = self.winfo_rootx() + max(20, self.winfo_width() - win.winfo_width() - 40)
        y = self.winfo_rooty() + max(20, self.winfo_height() - win.winfo_height() - 80)
        win.geometry(f"+{x}+{y}")

    def _config_from_ui(self) -> AppConfig:
        self.cfg.input_dir = self.vars["input_dir"].get().strip()
        self.cfg.output_dir = self.vars["output_dir"].get().strip()
        self._apply_daily_vars_to_config()
        self.cfg.logs_dir = self.vars["logs_dir"].get().strip()
        self.cfg.preset_file = self.vars["preset_file"].get().strip()
        self.cfg.pair_preset_file = self.vars["pair_preset_file"].get().strip()
        self.cfg.mode = self.vars["mode"].get()  # type: ignore[assignment]
        self.cfg.export.codec = self.vars["codec"].get()  # type: ignore[assignment]
        self.cfg.render_format = RENDER_FORMAT_VALUES.get(self.vars["render_format"].get(), "pair")  # type: ignore[assignment]
        self.cfg.parallel_workers = clamp_parallel_workers(self.vars["parallel_workers"].get())
        self.cfg.start_with_windows = bool(self.vars["start_with_windows"].get())
        self.cfg.auto_start_on_launch = bool(self.vars["auto_start_on_launch"].get())
        self.vars["parallel_workers"].set(str(self.cfg.parallel_workers))
        ensure_directories(self.cfg)
        self._refresh_daily_path_fields()
        self._gui_log_path = self.cfg.logs_path / "janela_teste.log"
        return self.cfg

    def _save_from_ui(self) -> None:
        cfg = self._config_from_ui()
        save_config(cfg, self.config_path)
        self._write("Configuração salva.")
        ok, message = configure_start_with_windows(bool(getattr(cfg, "start_with_windows", False)))
        self._write(message)

    def _set_status(self, msg: str) -> None:
        self.vars["status"].set(msg)
        level = self._message_level(msg)
        if hasattr(self, "render_visual"):
            self.render_visual.set_status(msg)
        if hasattr(self, "status_pill"):
            bg, fg = self._status_colors(level)
            self.status_pill.configure(bg=bg, fg=fg, highlightbackground=fg)
        if hasattr(self, "health_badge"):
            labels = {
                "error": "ERRO",
                "warning": "ATENCAO",
                "success": "OK",
                "info": "AUTO READY",
            }
            bg, fg = self._status_colors(level)
            self.health_badge.configure(
                text=labels.get(level, "AUTO READY"),
                bg=bg,
                fg=fg,
                highlightbackground=fg,
            )

    @staticmethod
    def _message_level(msg: str) -> str:
        text = msg.lower()
        if any(word in text for word in ["erro", "falha", "falhou", "bloqueado", "não foi possível", "nao foi possivel"]):
            return "error"
        if any(word in text for word in ["aguardando", "detectado", "parada", "parado", "ignorado", "nenhum", "pedido de parada", "quarentena"]):
            return "warning"
        if any(word in text for word in ["ok:", "finalizada", "finalizado", "salva", "iniciado", "aberta", "criado", "carregada", "aplicada"]):
            return "success"
        return "info"

    @staticmethod
    def _status_colors(level: str) -> tuple[str, str]:
        if level == "error":
            return COLORS["red_soft"], COLORS["red"]
        if level == "warning":
            return COLORS["yellow_soft"], COLORS["yellow"]
        if level == "success":
            return COLORS["green_soft"], COLORS["green"]
        return COLORS["blue_soft"], COLORS["cyan"]

    def _write(self, msg: str) -> None:
        quiet_message = (
            msg.startswith(("Nenhum vídeo", "Nenhum par", "Aguardando arquivo válido", "Fila aguardando"))
            or "pendente: aguardando todos os vídeos" in msg
        )
        if quiet_message:
            now = datetime.now()
            last_log_at = self._quiet_log_times.get(msg)
            if last_log_at is not None and (now - last_log_at).total_seconds() < 1800:
                return
            self._quiet_log_times[msg] = now
        self._queue.put(msg)

    def _drain_queue(self) -> None:
        while True:
            try:
                msg = self._queue.get_nowait()
            except queue.Empty:
                break
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._last_update_var.set(datetime.now().strftime("%H:%M:%S"))
            line = f"{stamp} | {msg}"
            self._set_status(msg)
            self.log.insert("end", line + "\n", self._message_level(msg))
            self.log.see("end")
            append_text_line(self._gui_log_path, line)
        self.after(200, self._drain_queue)

    def _run_available_candidates(self, cfg: AppConfig) -> bool:
        saw_error = False
        saw_waiting = False

        def write_status(msg: str) -> None:
            nonlocal saw_error, saw_waiting
            if msg.lower().startswith("erro"):
                saw_error = True
            if "aguardando arquivo válido" in msg.lower() or "pendente: aguardando" in msg.lower():
                saw_waiting = True
            self._write(msg)

        processed = render_available_candidates(cfg, write_status, should_stop=self._stop_event.is_set)
        if processed <= 0:
            if saw_error:
                self._write("Auto pausado: corrija o erro acima ou use codec auto/h264_amf/libx264 antes de tentar novamente.")
                self._stop_event.set()
                return False
            if saw_waiting:
                self._write("Fila aguardando arquivos de entrada válidos; nova verificação será automática.")
                return False
            self._write("Nenhum vídeo elegível encontrado.")
            return False
        self._write(f"Fila finalizada: {processed} vídeo(s) processado(s).")
        return True

    def _run_manual_82_candidates(self, cfg: AppConfig) -> bool:
        saw_error = False
        saw_waiting = False

        def write_status(msg: str) -> None:
            nonlocal saw_error, saw_waiting
            if msg.lower().startswith("erro"):
                saw_error = True
            if "aguardando arquivo válido" in msg.lower() or "pendente: aguardando" in msg.lower():
                saw_waiting = True
            self._write(msg)

        processed = render_manual_82_candidates(cfg, write_status, should_stop=self._stop_event.is_set)
        if processed <= 0:
            if saw_error:
                self._write("Render manual não finalizado: corrija o erro acima ou use codec auto/h264_amf/libx264.")
                return False
            if saw_waiting:
                self._write("Fila aguardando arquivos de entrada válidos; nova verificação será automática.")
                return False
            self._write("Nenhum vídeo 82 elegível encontrado na pasta selecionada.")
            return False
        self._write(f"Fila manual finalizada: {processed} vídeo(s) 82 processado(s).")
        return True

    def _run_both_candidates(self, cfg: AppConfig) -> bool:
        saw_error = False
        saw_waiting = False

        def write_status(msg: str) -> None:
            nonlocal saw_error, saw_waiting
            if msg.lower().startswith("erro"):
                saw_error = True
            if "aguardando arquivo válido" in msg.lower() or "pendente: aguardando" in msg.lower():
                saw_waiting = True
            self._write(msg)

        processed = render_both_candidates(cfg, write_status, should_stop=self._stop_event.is_set)
        if processed <= 0:
            if saw_error:
                self._write("Render de ambos não finalizado: corrija o erro informado acima.")
                return False
            if saw_waiting:
                self._write("Fila aguardando arquivos de entrada válidos; nova verificação será automática.")
                return False
            self._write("Nenhum par 81/82 elegível encontrado para gerar ambos.")
            return False
        self._write(f"Fila ambos finalizada: {processed} grupo(s) processado(s) nos dois formatos.")
        return True

    def _run_all_candidates(self, cfg: AppConfig) -> bool:
        saw_error = False
        saw_waiting = False

        def write_status(msg: str) -> None:
            nonlocal saw_error, saw_waiting
            if msg.lower().startswith("erro"):
                saw_error = True
            if "aguardando arquivo válido" in msg.lower() or "pendente: aguardando" in msg.lower():
                saw_waiting = True
            self._write(msg)

        processed = render_all_candidates(cfg, write_status, should_stop=self._stop_event.is_set)
        if processed <= 0:
            if saw_error:
                self._write("Render de todos não finalizado: corrija o erro acima ou ajuste o codec.")
                return False
            if saw_waiting:
                self._write("Fila aguardando arquivos de entrada válidos; nova verificação será automática.")
                return False
            self._write("Nenhum vídeo elegível encontrado na pasta selecionada.")
            return False
        self._write(f"Fila completa finalizada: {processed} vídeo(s) processado(s) individualmente.")
        return True

    def _run_selected_candidates(self, cfg: AppConfig) -> bool:
        render_format = effective_render_format(cfg)
        if render_format == "single":
            return self._run_manual_82_candidates(cfg)
        if render_format == "both":
            return self._run_both_candidates(cfg)
        if render_format == "all":
            return self._run_all_candidates(cfg)
        return self._run_available_candidates(cfg)

    def _render_once(self) -> None:
        if self._busy_lock.locked():
            self._write("Já existe um render em andamento.")
            return
        cfg = self._config_from_ui()
        save_config(cfg, self.config_path)
        self._stop_event.clear()
        self._set_processing_state(True)

        def worker() -> None:
            try:
                with self._busy_lock:
                    label = render_format_label(cfg)
                    self._write(f"Render de fila iniciado: formato {label}.")
                    self._run_selected_candidates(cfg)
                    self._write("Render de fila finalizado.")
            finally:
                self.after(0, lambda: self._set_processing_state(False))

        threading.Thread(target=worker, daemon=True).start()

    def _start_auto(self) -> None:
        if self._auto_thread and self._auto_thread.is_alive():
            self._write("Auto já está rodando.")
            return
        cfg = self._config_from_ui()
        save_config(cfg, self.config_path)
        self._stop_event.clear()
        self._set_processing_state(True)

        def worker() -> None:
            label = render_format_label(cfg)
            self._write(f"Auto iniciado: formato {label}.")
            recovery_tracker = IdleRescanTracker.from_config(cfg)
            while not self._stop_event.is_set():
                self._run_daily_processed_cleanup()
                if self._busy_lock.acquire(blocking=False):
                    try:
                        processed = self._run_selected_candidates(cfg)
                    finally:
                        self._busy_lock.release()
                    if recovery_tracker.record(processed):
                        result = rescan_pending_input(cfg)
                        self._write(result.message)
                        if result.pending_jobs and not self._stop_event.is_set():
                            continue
                    if not processed:
                        for _ in range(10):
                            if self._stop_event.is_set():
                                break
                            time.sleep(0.5)
                else:
                    time.sleep(0.5)
            self._write("Auto parado. Se havia render em andamento, ele foi finalizado antes de parar.")
            self.after(0, lambda: self._set_processing_state(False))

        self._auto_thread = threading.Thread(target=worker, daemon=True)
        self._auto_thread.start()

    def _stop_auto(self) -> None:
        self._stop_event.set()
        self._write("Pedido de parada recebido.")

    def _open_logs(self) -> None:
        cfg = self._config_from_ui()
        cfg.logs_path.mkdir(parents=True, exist_ok=True)
        self._open_folder(cfg.logs_path)
        self._write(f"Pasta de logs aberta: {cfg.logs_path}")

    def _zip_logs(self) -> None:
        cfg = self._config_from_ui()
        cfg.logs_path.mkdir(parents=True, exist_ok=True)
        zip_path = cfg.logs_path / f"logs_autorender_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for file in cfg.logs_path.rglob("*"):
                    if not file.is_file():
                        continue
                    if file.suffix.lower() == ".zip":
                        continue
                    zf.write(file, arcname=Path("logs") / file.relative_to(cfg.logs_path))
                config_file = Path(self.config_path)
                if config_file.exists():
                    zf.write(config_file, arcname=Path("config") / config_file.name)
            self._write(f"ZIP dos logs criado: {zip_path}")
            self._open_folder(cfg.logs_path)
        except Exception as exc:
            messagebox.showerror("Erro", f"Não foi possível gerar o ZIP dos logs:\n{exc}")
            self._write(f"ERRO ao gerar ZIP dos logs: {exc}")

    @staticmethod
    def _open_folder(path: Path) -> None:
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except AttributeError:
            subprocess.run(["xdg-open", str(path)], check=False)
        except OSError:
            pass

    def _close(self) -> None:
        if self._busy_lock.locked() or (self._auto_thread and self._auto_thread.is_alive()):
            self._stop_event.set()
            self._write("Fechamento bloqueado durante processamento. Pedido de parada enviado.")
            return
        self._stop_event.set()
        self._persist_window_geometry()
        self._write("Fechando janela.")
        self.after(250, self.destroy)


def launch_ui(config_path: str = "config/settings.json") -> None:
    app = AutoRenderUI(config_path)
    app.mainloop()
