from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Literal, Optional

RenderMode = Literal["ultra", "pdv", "balanced", "quality"]
PresetType = Literal["auto", "alpha", "green"]
ScaleMode = Literal["fill", "fit", "fit_blur", "crop"]
KeyerMethod = Literal["auto", "chromakey", "colorkey"]
DurationMode = Literal["input", "preset", "limit"]
VideoCodec = Literal["auto", "h264_nvenc", "h264_amf", "libx264"]
RenderFormat = Literal["single", "pair", "both", "all"]
MAX_PARALLEL_WORKERS = 2
DEFAULT_SPECIAL_CYCLE_FOLDERS = ["Manual_Retry", "Manual"]
DOWNLOADER_MANUAL_CYCLE_SUFFIX = "_MANUAL_TODAS"
DEFAULT_DAILY_INPUT_FORMAT = "%Y-%m-%d"
DEFAULT_DAILY_OUTPUT_FORMAT = "%d-%m-%y"


def clamp_parallel_workers(value: Any, default: int = 1) -> int:
    try:
        workers = int(value)
    except Exception:
        workers = default
    return max(1, min(MAX_PARALLEL_WORKERS, workers))


def normalize_special_cycle_folders(value: Any) -> list[str]:
    values = value if isinstance(value, list) else []
    result: list[str] = []
    seen: set[str] = set()
    for item in [*values, *DEFAULT_SPECIAL_CYCLE_FOLDERS]:
        name = str(item).strip()
        key = name.lower()
        if name and key not in seen:
            result.append(name)
            seen.add(key)
    return result


def is_downloader_cycle_folder_name(value: Any) -> bool:
    """Reconhece os nomes de lote criados automaticamente pelos downloaders."""
    name = str(value or "").strip().upper()
    return bool(
        name.endswith("_AUTO")
        or name == DOWNLOADER_MANUAL_CYCLE_SUFFIX.lstrip("_")
        or name.endswith(DOWNLOADER_MANUAL_CYCLE_SUFFIX)
    )


def is_recognized_cycle_folder_name(value: Any, special_folders: Any = None) -> bool:
    if is_downloader_cycle_folder_name(value):
        return True
    names = normalize_special_cycle_folders(special_folders)
    normalized = str(value or "").strip().lower()
    return normalized in {name.lower() for name in names}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "sim", "on"}
    return bool(value)


def _as_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip().replace(",", "."))
    except Exception:
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(round(_as_float(value, float(default))))
    except Exception:
        return default


@dataclass
class ChromaKeyConfig:
    color: str = "0x00FF00"
    # Para presets .MOV em qtrle/rgb24, colorkey costuma ser mais preciso que chromakey,
    # porque trabalha em RGB antes de converter o resultado final para yuv420p.
    method: KeyerMethod = "auto"
    similarity: float = 0.16
    blend: float = 0.02
    despill: bool = True
    despill_mix: float = 0.55
    despill_expand: float = 0.15
    despill_green: float = -1.0
    despill_alpha: bool = False


@dataclass
class ExportConfig:
    width: int = 1080
    height: int = 1920
    fps: str = "30"  # "original", "29.97", "30", "60"
    # auto = usa GPU quando disponível: NVIDIA/NVENC, AMD/AMF; senão usa libx264 em CPU.
    codec: VideoCodec = "auto"
    # "copy" preserva o áudio original da GoPro sem recompressão.
    # Use "aac" somente se precisar forçar recodificação.
    audio_codec: str = "copy"
    audio_bitrate: str = "192k"
    audio_sample_rate: int = 48000
    audio_channels: int = 2
    duration_mode: DurationMode = "limit"
    max_duration_seconds: int = 65
    scale_mode: ScaleMode = "fit_blur"
    preset_type: PresetType = "auto"
    chromakey: ChromaKeyConfig = field(default_factory=ChromaKeyConfig)


@dataclass
class PairLayoutConfig:
    # Tempos em segundos. Aceita decimal, ex.: 5.35.
    video81_start_seconds: float = 5.0
    video81_end_seconds: float = 29.0
    video81_x: int = 806
    video81_y: int = 502
    video81_width: int = 248
    video81_height: int = 320
    video81_crop_top: int = 8
    # Câmera 82 (janela grande). start atrasa o frame 0 na linha do tempo; trim corta o início do arquivo.
    video82_start_seconds: float = 0.0
    video82_trim_start_seconds: float = 0.0
    video82_x: int = 0
    video82_y: int = 660
    video82_width: int = 1080
    video82_height: int = 615


@dataclass
class ModeProfile:
    name: str
    nvenc_preset: str
    amf_quality: str
    x264_preset: str
    bitrate: str
    description: str


DEFAULT_PROFILES: Dict[str, ModeProfile] = {
    "ultra": ModeProfile("ultra", "p1", "speed", "ultrafast", "4M", "Menor arquivo e menor exigência de encoder."),
    "pdv": ModeProfile("pdv", "p4", "balanced", "fast", "4M", "Qualidade melhor que ultra mantendo tamanho próximo do ultra."),
    "balanced": ModeProfile("balanced", "p2", "balanced", "veryfast", "6M", "Melhor custo-benefício quando tamanho não é prioridade."),
    "quality": ModeProfile("quality", "p4", "quality", "faster", "8M", "Mais qualidade, com arquivo maior."),
}


@dataclass
class AppConfig:
    project_root: str = "."
    preset_dir: str = "preset"
    preset_file: str = "preset.mov"
    pair_preset_file: str = ""
    input_dir: str = "entrada"
    output_dir: str = "saida"
    daily_input_enabled: bool = False
    daily_input_root: str = ""
    daily_input_format: str = DEFAULT_DAILY_INPUT_FORMAT
    daily_output_enabled: bool = False
    daily_output_root: str = ""
    daily_output_format: str = DEFAULT_DAILY_OUTPUT_FORMAT
    processed_dir: str = "processados"
    error_dir: str = "erros"
    quarantine_dir: str = "quarentena"
    logs_dir: str = "logs"
    history_file: str = "logs/render_history.json"
    processed_cleanup_last_run: str = ""
    quarantine_cleanup_last_run: str = ""
    quarantine_attempts: int = 6
    window_geometry: str = ""
    mode: RenderMode = "balanced"
    render_format: RenderFormat = "pair"
    stable_check_seconds: float = 3.0
    stable_check_interval: float = 1.0
    stable_check_max_wait_seconds: float = 120.0
    ffmpeg_timeout_seconds: int = 1800
    ffprobe_timeout_seconds: int = 30
    move_original_on_success: bool = True
    move_original_on_error: bool = False
    recursive_input: bool = True
    group_output_by_cycle_folder: bool = True
    preserve_cycle_folder_when_moving: bool = True
    history_enabled: bool = True
    idle_rescan_checks: int = 6
    start_with_windows: bool = False
    auto_start_on_launch: bool = False
    startup_safety_reset_done: bool = False
    update_dir: str = "update"
    local_staging_enabled: bool = True
    local_staging_dir: str = "render_staging"
    # Trava de segurança: este projeto não permite mais de 2 renders simultâneos.
    # 1 = comportamento seguro/antigo. 2 = limite máximo validado para este hardware.
    parallel_workers: int = 1
    loose_output_folder_by_render_time: bool = True
    loose_output_time_format: str = "%H%M%S_RENDER"
    # Pastas especiais dentro de entrada que devem ser tratadas como lote/ciclo,
    # mesmo sem terminar com _AUTO. Ex.: Manual_Retry e Manual criadas pelo fluxo GoPro.
    special_cycle_folders: list[str] = field(default_factory=lambda: list(DEFAULT_SPECIAL_CYCLE_FOLDERS))
    pair_layout: PairLayoutConfig = field(default_factory=PairLayoutConfig)
    export: ExportConfig = field(default_factory=ExportConfig)

    def root_path(self) -> Path:
        return Path(self.project_root).resolve()

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return self.root_path() / path

    def daily_path(self, enabled: bool, root: str, date_format: str, fallback_format: str) -> Path | None:
        root_value = str(root or "").strip()
        if not enabled or not root_value:
            return None
        fmt = str(date_format or "").strip() or fallback_format
        return self.resolve_path(root_value) / datetime.now().strftime(fmt)

    def _resolve_preset_path(self, preset_file: str, fallback_file: str = "") -> Path:
        """Resolve um preset .MOV de forma tolerante.

        Regras:
        1. Se o arquivo configurado apontar para um arquivo existente, usa ele.
        2. Se preset_file for relativo e existir dentro de preset_dir, usa ele.
        3. Se houver fallback, tenta o fallback.
        4. Se não existir, procura automaticamente o primeiro .mov dentro de preset_dir.

        Isso evita erro quando o projeto muda de unidade/pasta ou quando o usuário
        troca o nome do arquivo .mov dentro da pasta preset.
        """
        preset_dir = self.resolve_path(self.preset_dir)
        preset_value = str(preset_file or "").strip()

        if preset_value:
            preset = Path(preset_value)
            candidate = preset if preset.is_absolute() else preset_dir / preset
            if candidate.exists():
                return candidate

        fallback_value = str(fallback_file or "").strip()
        if fallback_value and fallback_value != preset_value:
            preset = Path(fallback_value)
            candidate = preset if preset.is_absolute() else preset_dir / preset
            if candidate.exists():
                return candidate

        movs = sorted(preset_dir.glob("*.mov"), key=lambda item: item.name.lower())
        if movs:
            return movs[0]

        # Mantém um caminho previsível para a mensagem de erro do renderer.
        if preset_value:
            preset = Path(preset_value)
            return preset if preset.is_absolute() else preset_dir / preset
        return preset_dir / "preset.mov"

    @property
    def preset_path(self) -> Path:
        return self._resolve_preset_path(self.preset_file)

    @property
    def pair_preset_path(self) -> Path:
        return self._resolve_preset_path(self.pair_preset_file, self.preset_file)

    @property
    def input_path(self) -> Path:
        daily = self.daily_path(
            self.daily_input_enabled,
            self.daily_input_root,
            self.daily_input_format,
            DEFAULT_DAILY_INPUT_FORMAT,
        )
        if daily:
            return daily
        return self.resolve_path(self.input_dir)

    @property
    def output_path(self) -> Path:
        daily = self.daily_path(
            self.daily_output_enabled,
            self.daily_output_root,
            self.daily_output_format,
            DEFAULT_DAILY_OUTPUT_FORMAT,
        )
        if daily:
            return daily
        return self.resolve_path(self.output_dir)

    @property
    def processed_path(self) -> Path:
        return self.resolve_path(self.processed_dir)

    @property
    def error_path(self) -> Path:
        return self.resolve_path(self.error_dir)

    @property
    def quarantine_path(self) -> Path:
        return self.resolve_path(self.quarantine_dir)

    @property
    def logs_path(self) -> Path:
        return self.resolve_path(self.logs_dir)

    @property
    def history_path(self) -> Path:
        return self.resolve_path(self.history_file)

    @property
    def update_path(self) -> Path:
        return self.resolve_path(self.update_dir)

    def available_preset_paths(self) -> list[Path]:
        """Retorna os presets .MOV existentes, sem duplicar caminhos configurados."""
        preset_dir = self.resolve_path(self.preset_dir)
        candidates: list[Path] = []
        try:
            candidates.extend(
                path for path in preset_dir.iterdir()
                if path.is_file() and path.suffix.lower() == ".mov"
            )
        except OSError:
            pass

        for value in (self.preset_file, self.pair_preset_file):
            text = str(value or "").strip()
            if not text:
                continue
            path = Path(text)
            candidate = path if path.is_absolute() else preset_dir / path
            try:
                if candidate.is_file() and candidate.suffix.lower() == ".mov":
                    candidates.append(candidate)
            except OSError:
                continue

        unique: dict[str, Path] = {}
        for path in candidates:
            try:
                key = str(path.resolve()).lower()
            except OSError:
                key = str(path).lower()
            unique.setdefault(key, path)
        return sorted(unique.values(), key=lambda path: str(path).lower())


def effective_render_format(config: AppConfig) -> RenderFormat:
    """Com um único preset, cada vídeo da pasta vira uma saída individual."""
    if len(config.available_preset_paths()) == 1:
        return "all"
    return config.render_format


def _from_dict(cls, data: Dict[str, Any]):
    kwargs = {}
    for key, field_info in cls.__dataclass_fields__.items():  # type: ignore[attr-defined]
        if key in data:
            kwargs[key] = data[key]
    return cls(**kwargs)


def app_config_from_dict(data: Dict[str, Any]) -> AppConfig:
    data = dict(data)
    if data.get("mode") not in ("ultra", "pdv", "balanced", "quality"):
        data["mode"] = "balanced"
    if data.get("render_format") not in ("single", "pair", "both", "all"):
        data["render_format"] = "pair"

    pair_layout_data = dict(data.get("pair_layout") or {})
    pair_layout = _from_dict(PairLayoutConfig, pair_layout_data)
    pair_layout.video81_start_seconds = _as_float(pair_layout.video81_start_seconds, 5.0)
    pair_layout.video81_end_seconds = _as_float(pair_layout.video81_end_seconds, 29.0)
    pair_layout.video81_x = _as_int(pair_layout.video81_x, 806)
    pair_layout.video81_y = _as_int(pair_layout.video81_y, 502)
    pair_layout.video81_width = _as_int(pair_layout.video81_width, 248)
    pair_layout.video81_height = _as_int(pair_layout.video81_height, 320)
    pair_layout.video81_crop_top = _as_int(pair_layout.video81_crop_top, 8)
    pair_layout.video82_start_seconds = _as_float(pair_layout.video82_start_seconds, 0.0)
    pair_layout.video82_trim_start_seconds = _as_float(pair_layout.video82_trim_start_seconds, 0.0)
    pair_layout.video82_x = _as_int(pair_layout.video82_x, 0)
    pair_layout.video82_y = _as_int(pair_layout.video82_y, 660)
    pair_layout.video82_width = _as_int(pair_layout.video82_width, 1080)
    pair_layout.video82_height = _as_int(pair_layout.video82_height, 615)
    data["pair_layout"] = pair_layout

    export_data = dict(data.get("export") or {})
    chroma_data = export_data.get("chromakey") or {}
    if chroma_data.get("method") not in ("auto", "chromakey", "colorkey"):
        chroma_data["method"] = "auto"
    export_data["chromakey"] = _from_dict(ChromaKeyConfig, chroma_data)
    if export_data.get("codec") not in ("auto", "h264_nvenc", "h264_amf", "libx264"):
        export_data["codec"] = "auto"
    if export_data.get("scale_mode") not in ("fill", "fit", "fit_blur", "crop"):
        export_data["scale_mode"] = "fit_blur"
    if export_data.get("preset_type") not in ("auto", "alpha", "green"):
        export_data["preset_type"] = "auto"
    if export_data.get("duration_mode") not in ("input", "preset", "limit"):
        export_data["duration_mode"] = "limit"
    data["export"] = _from_dict(ExportConfig, export_data)
    data["export"].width = max(16, _as_int(data["export"].width, 1080))
    data["export"].height = max(16, _as_int(data["export"].height, 1920))
    data["export"].max_duration_seconds = max(1, _as_int(data["export"].max_duration_seconds, 65))
    data["parallel_workers"] = clamp_parallel_workers(data.get("parallel_workers", 1))
    data["special_cycle_folders"] = normalize_special_cycle_folders(data.get("special_cycle_folders"))
    data["stable_check_seconds"] = max(0.0, _as_float(data.get("stable_check_seconds", 3.0), 3.0))
    data["stable_check_interval"] = max(0.2, _as_float(data.get("stable_check_interval", 1.0), 1.0))
    data["stable_check_max_wait_seconds"] = max(
        data["stable_check_seconds"],
        _as_float(data.get("stable_check_max_wait_seconds", 120.0), 120.0),
    )
    data["ffmpeg_timeout_seconds"] = max(0, _as_int(data.get("ffmpeg_timeout_seconds", 1800), 1800))
    data["ffprobe_timeout_seconds"] = max(1, _as_int(data.get("ffprobe_timeout_seconds", 30), 30))
    data["daily_input_enabled"] = _as_bool(data.get("daily_input_enabled", False))
    data["daily_output_enabled"] = _as_bool(data.get("daily_output_enabled", False))
    data["move_original_on_success"] = _as_bool(data.get("move_original_on_success", True))
    data["move_original_on_error"] = _as_bool(data.get("move_original_on_error", False))
    data["recursive_input"] = _as_bool(data.get("recursive_input", True))
    data["group_output_by_cycle_folder"] = _as_bool(data.get("group_output_by_cycle_folder", True))
    data["preserve_cycle_folder_when_moving"] = _as_bool(data.get("preserve_cycle_folder_when_moving", True))
    data["history_enabled"] = _as_bool(data.get("history_enabled", True))
    data["idle_rescan_checks"] = max(1, min(120, _as_int(data.get("idle_rescan_checks", 6), 6)))
    data["quarantine_attempts"] = max(2, min(20, _as_int(data.get("quarantine_attempts", 6), 6)))
    data["window_geometry"] = str(data.get("window_geometry") or "").strip()
    data["loose_output_folder_by_render_time"] = _as_bool(data.get("loose_output_folder_by_render_time", True))
    data["start_with_windows"] = _as_bool(data.get("start_with_windows", False))
    data["auto_start_on_launch"] = _as_bool(data.get("auto_start_on_launch", False))
    data["startup_safety_reset_done"] = _as_bool(data.get("startup_safety_reset_done", False))
    data["local_staging_enabled"] = _as_bool(data.get("local_staging_enabled", True))
    if not str(data.get("daily_input_format") or "").strip():
        data["daily_input_format"] = DEFAULT_DAILY_INPUT_FORMAT
    if not str(data.get("daily_output_format") or "").strip():
        data["daily_output_format"] = DEFAULT_DAILY_OUTPUT_FORMAT
    return _from_dict(AppConfig, data)


def load_config(path: str | Path = "config/settings.json") -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        cfg = AppConfig(project_root=str(config_path.parent.parent.resolve()))
        save_config(cfg, config_path)
        return cfg
    # utf-8-sig aceita JSON salvo com BOM pelo PowerShell antigo
    # e também lê arquivos UTF-8 normais sem BOM.
    with config_path.open("r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    return app_config_from_dict(data)


def save_config(config: AppConfig, path: str | Path = "config/settings.json") -> None:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as fh:
        json.dump(asdict(config), fh, ensure_ascii=False, indent=2)


def ensure_directories(config: AppConfig) -> None:
    for path in [
        config.resolve_path(config.preset_dir),
        config.input_path,
        config.output_path,
        config.processed_path,
        config.error_path,
        config.quarantine_path,
        config.logs_path,
        config.update_path,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def get_profile(mode: Optional[RenderMode]) -> ModeProfile:
    return DEFAULT_PROFILES.get(mode or "balanced", DEFAULT_PROFILES["balanced"])
