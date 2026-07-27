from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import threading
import time
from functools import lru_cache
from pathlib import Path
from datetime import datetime
from typing import Any, List, Optional, Tuple

from backend.config import (
    AppConfig,
    DEFAULT_SPECIAL_CYCLE_FOLDERS,
    DOWNLOADER_MANUAL_CYCLE_SUFFIX,
    RenderMode,
    VideoCodec,
    get_profile,
    is_recognized_cycle_folder_name,
)
from backend.ffprobe_reader import VideoInfo
from backend.subprocess_utils import hidden_console_kwargs


_RUNTIME_TEST_CACHE: dict[str, tuple[float, tuple[bool, str]]] = {}
_RUNTIME_TEST_CACHE_LOCK = threading.Lock()
_RUNTIME_SUCCESS_TTL_SECONDS = 300.0
_RUNTIME_FAILURE_TTL_SECONDS = 30.0


def _cached_runtime_test(name: str, probe) -> Tuple[bool, str]:
    now = time.monotonic()
    with _RUNTIME_TEST_CACHE_LOCK:
        cached = _RUNTIME_TEST_CACHE.get(name)
        if cached and cached[0] > now:
            return cached[1]

    result = probe()
    ttl = _RUNTIME_SUCCESS_TTL_SECONDS if result[0] else _RUNTIME_FAILURE_TTL_SECONDS
    with _RUNTIME_TEST_CACHE_LOCK:
        _RUNTIME_TEST_CACHE[name] = (time.monotonic() + ttl, result)
    return result


_OUTPUT_PATH_LOCK = threading.Lock()
_RESERVED_OUTPUT_PATHS: set[str] = set()


class FFmpegBuildError(RuntimeError):
    pass


def _find_executable(name: str, env_var: str) -> str | None:
    """Procura executáveis primeiro no projeto, depois no PATH."""
    candidates = []
    env_value = os.environ.get(env_var)
    if env_value:
        candidates.append(Path(env_value))

    project_root = Path(__file__).resolve().parent.parent
    candidates.extend([
        project_root / "bin" / name,
        Path.cwd() / "bin" / name,
    ])

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return shutil.which(name)


@lru_cache(maxsize=1)
def require_ffmpeg() -> str:
    executable = _find_executable("ffmpeg.exe", "AUTORENDER_FFMPEG") or _find_executable("ffmpeg", "AUTORENDER_FFMPEG")
    if not executable:
        raise FFmpegBuildError(
            "FFmpeg não encontrado. Coloque ffmpeg.exe em bin\\ ou adicione o FFmpeg ao PATH."
        )
    return executable


@lru_cache(maxsize=1)
def ffmpeg_supported_encoders() -> Tuple[set[str], str]:
    ffmpeg = require_ffmpeg()
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            **hidden_console_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegBuildError("FFmpeg demorou demais para listar encoders.") from exc
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    encoders = set()
    for line in output.splitlines():
        # Linhas de encoder costumam ter o nome na segunda coluna. Ex.: " V....D h264_nvenc ..."
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            encoders.add(parts[1])
    return encoders, output


def ffmpeg_supports_nvenc() -> Tuple[bool, str]:
    encoders, output = ffmpeg_supported_encoders()
    return "h264_nvenc" in encoders or "h264_nvenc" in output, output


def ffmpeg_supports_amf() -> Tuple[bool, str]:
    encoders, output = ffmpeg_supported_encoders()
    return "h264_amf" in encoders or "h264_amf" in output, output


def ffmpeg_supports_libx264() -> Tuple[bool, str]:
    encoders, output = ffmpeg_supported_encoders()
    return "libx264" in encoders or "libx264" in output, output


def _probe_nvenc_runtime() -> Tuple[bool, str]:
    """
    Testa NVENC de verdade.
    Só aparecer em `ffmpeg -encoders` não basta: em PC sem driver/GPU NVIDIA, o encoder lista,
    mas falha na hora com "Cannot load nvcuda.dll".
    """
    ok_listed, listed_output = ffmpeg_supports_nvenc()
    if not ok_listed:
        return False, "h264_nvenc não aparece em ffmpeg -encoders.\n" + listed_output

    ffmpeg = require_ffmpeg()
    sink = "NUL" if platform.system().lower().startswith("win") else "/dev/null"
    # Atenção: algumas versões do NVENC recusam quadros muito pequenos.
    # O teste antigo usava 64x64 e, em placas como RTX 4060, isso pode retornar:
    # "Frame Dimension less than the minimum supported value", mesmo com NVENC disponível.
    # Usamos 320x240 + yuv420p para validar o runtime com uma dimensão segura e leve.
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "lavfi",
        "-i", "color=c=black:s=320x240:d=0.1:r=1",
        "-frames:v", "1",
        "-vf", "format=yuv420p",
        "-an",
        "-c:v", "h264_nvenc",
        "-preset", "p2",
        "-f", "h264",
        sink,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            **hidden_console_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return False, "Teste NVENC excedeu 15s."
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc.returncode == 0, output.strip()


def test_nvenc_runtime() -> Tuple[bool, str]:
    return _cached_runtime_test("nvenc", _probe_nvenc_runtime)


def _probe_amf_runtime() -> Tuple[bool, str]:
    """Testa o encoder AMD AMF além da presença na lista do FFmpeg."""
    ok_listed, listed_output = ffmpeg_supports_amf()
    if not ok_listed:
        return False, "h264_amf não aparece em ffmpeg -encoders.\n" + listed_output

    ffmpeg = require_ffmpeg()
    sink = "NUL" if platform.system().lower().startswith("win") else "/dev/null"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "lavfi",
        "-i", "color=c=black:s=320x240:d=0.1:r=1",
        "-frames:v", "1",
        "-vf", "format=nv12",
        "-an",
        "-c:v", "h264_amf",
        "-quality", "speed",
        "-f", "h264",
        sink,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            **hidden_console_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return False, "Teste AMD AMF excedeu 15s."
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc.returncode == 0, output.strip()


def test_amf_runtime() -> Tuple[bool, str]:
    return _cached_runtime_test("amf", _probe_amf_runtime)


def choose_video_codec(requested: VideoCodec) -> Tuple[str, str]:
    """
    Retorna (codec_real, aviso).
    - auto: usa h264_nvenc, depois h264_amf; caso contrário usa libx264.
    - h264_nvenc: exige NVENC funcionando.
    - h264_amf: exige AMD AMF funcionando.
    - libx264: usa CPU.
    """
    requested = requested or "auto"  # type: ignore[assignment]
    if requested == "libx264":
        ok, output = ffmpeg_supports_libx264()
        if not ok:
            raise FFmpegBuildError("Seu FFmpeg não mostra suporte a libx264. Baixe uma build completa do FFmpeg.")
        return "libx264", "Usando CPU/libx264 por configuração."

    if requested == "h264_nvenc":
        ok, output = test_nvenc_runtime()
        if not ok:
            raise FFmpegBuildError(
                "h264_nvenc foi solicitado, mas o NVENC não funcionou neste PC. "
                "Pode ser driver/GPU NVIDIA indisponível, nvcuda.dll ausente, ou parâmetro recusado pelo encoder. "
                "Para continuar, use --codec auto, --codec h264_amf ou --codec libx264.\n" + output
            )
        return "h264_nvenc", "Usando GPU/NVENC por configuração."

    if requested == "h264_amf":
        ok, output = test_amf_runtime()
        if not ok:
            raise FFmpegBuildError(
                "h264_amf foi solicitado, mas o encoder AMD AMF não funcionou neste PC. "
                "Pode ser driver AMD indisponível, GPU AMD sem suporte, FFmpeg sem AMF funcional, "
                "ou sessão remota sem acesso ao hardware. Para continuar, use --codec auto ou --codec libx264.\n"
                + output
            )
        return "h264_amf", "Usando GPU/AMD AMF por configuração."

    # auto
    nvenc_ok, nvenc_output = test_nvenc_runtime()
    if nvenc_ok:
        return "h264_nvenc", "Usando GPU/NVENC detectado automaticamente."

    amf_ok, amf_output = test_amf_runtime()
    if amf_ok:
        return "h264_amf", "Usando GPU/AMD AMF detectado automaticamente."

    ok_x264, x264_output = ffmpeg_supports_libx264()
    if ok_x264:
        return "libx264", "GPU indisponível; usando CPU/libx264 automaticamente."

    raise FFmpegBuildError(
        "Nenhum encoder H.264 utilizável foi encontrado. "
        "NVENC/AMF falharam e libx264 não aparece no FFmpeg.\nNVENC:\n"
        + nvenc_output
        + "\nAMF:\n"
        + amf_output
        + "\nlibx264:\n"
        + x264_output
    )


def _safe_name_part(value: str) -> str:
    """Mantém o nome previsível no Windows sem inventar caracteres problemáticos."""
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value.strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned or "preset"


def _first_relative_folder(
    input_path: Path,
    input_root: Optional[Path],
    special_cycle_folders: Optional[list[str]] = None,
) -> Optional[str]:
    """Retorna a pasta de ciclo logo abaixo de entrada, se o vídeo estiver dentro dela.

    Pastas reconhecidas:
    - *_AUTO, fluxo normal do download automático.
    - *_MANUAL_TODAS, fluxo de download manual de todas as câmeras.
    - nomes especiais configurados, como Manual ou Manual_Retry.
    """
    if not input_root:
        return None
    try:
        relative = input_path.resolve().relative_to(input_root.resolve())
    except ValueError:
        return None
    if len(relative.parts) < 2:
        return None
    first = relative.parts[0]
    folder_names = special_cycle_folders if special_cycle_folders is not None else DEFAULT_SPECIAL_CYCLE_FOLDERS
    if is_recognized_cycle_folder_name(first, folder_names):
        return first
    return None


def cycle_output_folder_name(folder_name: str) -> str:
    """Converte nomes de ciclo dos downloaders em uma pasta *_RENDER previsível."""
    name = folder_name.strip()
    if not name:
        return "RENDER"
    if re.search(re.escape(DOWNLOADER_MANUAL_CYCLE_SUFFIX) + r"$", name, flags=re.IGNORECASE):
        name = re.sub(
            re.escape(DOWNLOADER_MANUAL_CYCLE_SUFFIX) + r"$",
            "_RENDER",
            name,
            flags=re.IGNORECASE,
        )
    elif name.upper() == DOWNLOADER_MANUAL_CYCLE_SUFFIX.lstrip("_"):
        name = "RENDER"
    elif re.search(r"_AUTO$", name, flags=re.IGNORECASE):
        name = re.sub(r"_AUTO$", "_RENDER", name, flags=re.IGNORECASE)
    elif re.search(r"AUTO$", name, flags=re.IGNORECASE):
        name = re.sub(r"AUTO$", "RENDER", name, flags=re.IGNORECASE)
    else:
        name = f"{name}_RENDER"
    return _safe_name_part(name)


def build_output_path(
    input_path: Path,
    output_dir: Path,
    preset_path: Optional[Path] = None,
    input_root: Optional[Path] = None,
    group_by_cycle_folder: bool = True,
    loose_output_folder_by_render_time: bool = False,
    loose_output_time_format: str = "%H%M%S_RENDER",
    render_started_at: Optional[datetime] = None,
    special_cycle_folders: Optional[list[str]] = None,
    output_stem: Optional[str] = None,
) -> Path:
    cycle_folder = _first_relative_folder(input_path, input_root, special_cycle_folders) if group_by_cycle_folder else None
    if cycle_folder:
        output_dir = output_dir / cycle_output_folder_name(cycle_folder)
    elif loose_output_folder_by_render_time:
        started = render_started_at or datetime.now()
        output_dir = output_dir / _safe_name_part(started.strftime(loose_output_time_format))

    output_dir.mkdir(parents=True, exist_ok=True)
    preset_stem = _safe_name_part((preset_path or Path("preset")).stem)
    input_stem = _safe_name_part(output_stem) if output_stem else _safe_name_part(input_path.stem)
    base = f"{input_stem}_{preset_stem}"

    # Em modo paralelo, dois renders com o mesmo stem poderiam escolher o mesmo nome
    # antes do FFmpeg criar o arquivo. A reserva em memória evita colisão dentro
    # do mesmo processo; o FFmpeg com -n continua protegendo contra colisão externa.
    with _OUTPUT_PATH_LOCK:
        idx = 0
        while True:
            suffix = "" if idx == 0 else f"_{idx:03d}"
            candidate = output_dir / f"{base}{suffix}.mp4"
            key = str(candidate.resolve()).lower()
            if not candidate.exists() and key not in _RESERVED_OUTPUT_PATHS:
                _RESERVED_OUTPUT_PATHS.add(key)
                return candidate
            idx += 1


def release_reserved_output_path(output_path: Path) -> None:
    """Libera uma reserva criada por build_output_path dentro deste processo."""
    with _OUTPUT_PATH_LOCK:
        try:
            _RESERVED_OUTPUT_PATHS.discard(str(output_path.resolve()).lower())
        except OSError:
            _RESERVED_OUTPUT_PATHS.discard(str(output_path).lower())


def _scale_filter(config: AppConfig) -> str:
    return _scale_filter_for_size(config, config.export.width, config.export.height)


def _scale_filter_for_size(config: AppConfig, width: int, height: int) -> str:
    mode = config.export.scale_mode
    if mode == "fit":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        )
    # fill/crop cortam sobras para preencher todo o quadro.
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )


def _fps_filter(config: AppConfig) -> str:
    fps = str(config.export.fps).lower()
    if fps in ("original", "keep", "manter"):
        return ""
    return f",fps={fps}"


def _base_graph_segment(config: AppConfig) -> str:
    return _video_graph_segment(config, "[0:v]", "base", config.export.width, config.export.height)


def _ffmpeg_number(value: float) -> str:
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def _video_graph_segment(config: AppConfig, input_label: str, output_label: str, width: int, height: int) -> str:
    """Monta a parte do filtergraph que cria o vídeo base.

    Modos:
    - fill/crop: preenche cortando sobras.
    - fit: mostra o vídeo inteiro, mas pode criar bordas.
    - fit_blur: mostra o vídeo inteiro sem cortar e usa uma versão ampliada/desfocada
      do próprio vídeo como fundo para continuar preenchendo 1080x1920.

    Observação prática: para proporções diferentes, não existe preencher 100% e não cortar
    sem escolher uma destas compensações: distorcer, criar borda ou usar fundo.
    Aqui usamos fundo blur para preservar o vídeo inteiro sem distorção.
    """
    mode = config.export.scale_mode

    if mode == "fit_blur":
        fps = _fps_filter(config).lstrip(",")
        fps_part = f"{fps}," if fps else ""
        # O fundo termina fortemente desfocado. Processá-lo a 1/4 da resolução
        # e ampliá-lo depois preserva o primeiro plano/preset em resolução total,
        # mas reduz muito o custo dos filtros na CPU.
        blur_width = max(16, width // 4)
        blur_height = max(16, height // 4)
        return (
            f"{input_label}{fps_part}setpts=PTS-STARTPTS,split=2[{output_label}_bgsrc][{output_label}_fgsrc];"
            f"[{output_label}_bgsrc]scale={blur_width}:{blur_height}:force_original_aspect_ratio=increase,"
            f"crop={blur_width}:{blur_height},boxblur=8:1,scale={width}:{height}[{output_label}_bg];"
            f"[{output_label}_fgsrc]scale={width}:{height}:force_original_aspect_ratio=decrease[{output_label}_fg];"
            f"[{output_label}_bg][{output_label}_fg]overlay=(W-w)/2:(H-h)/2[{output_label}];"
        )

    base_filter = _scale_filter_for_size(config, width, height) + _fps_filter(config)
    return f"{input_label}{base_filter},setpts=PTS-STARTPTS[{output_label}];"


def _input_seek_before_args(seconds: float) -> List[str]:
    if seconds <= 0:
        return []
    return ["-ss", _ffmpeg_number(seconds)]


def _dual_base_graph_segment(config: AppConfig) -> str:
    width = config.export.width
    height = config.export.height
    fps = str(config.export.fps).lower()
    source_fps = "30" if fps in ("original", "keep", "manter") else fps

    # Layout do preset Criamigos de dois vídeos:
    # câmera 82 no recorte central grande; câmera 81 na janela menor acima à direita.
    layout = config.pair_layout
    video_82 = _dual_video_82_slot(config)
    video_81 = _dual_video_81_slot(config)

    def slot(
        input_label: str,
        output_label: str,
        slot_width: int,
        slot_height: int,
        *,
        delay_seconds: float = 0,
        trim_start_seconds: float = 0,
        crop_top: int = 0,
    ) -> str:
        fps_filter = _fps_filter(config).lstrip(",")
        fps_part = f"{fps_filter}," if fps_filter else ""
        trim_part = (
            f"trim=start={_ffmpeg_number(trim_start_seconds)},"
            if trim_start_seconds > 0
            else ""
        )
        setpts = "PTS-STARTPTS" if delay_seconds <= 0 else f"PTS-STARTPTS+{_ffmpeg_number(delay_seconds)}/TB"
        scaled_height = slot_height + crop_top
        crop_filter = (
            f"crop={slot_width}:{slot_height}:(iw-ow)/2:{crop_top}"
            if crop_top > 0
            else f"crop={slot_width}:{slot_height}"
        )
        return (
            f"{input_label}{fps_part}{trim_part}setpts={setpts},"
            f"scale={slot_width}:{scaled_height}:force_original_aspect_ratio=increase,"
            f"{crop_filter},setsar=1[{output_label}];"
        )

    return (
        f"color=c=black:s={width}x{height}:r={source_fps}[canvas];"
        + slot(
            "[1:v]",
            "video82",
            video_82["w"],
            video_82["h"],
            delay_seconds=layout.video82_start_seconds,
        )
        + slot(
            "[0:v]",
            "video81",
            video_81["w"],
            video_81["h"],
            delay_seconds=layout.video81_start_seconds,
            crop_top=layout.video81_crop_top,
        )
        + f"[canvas][video82]overlay={video_82['x']}:{video_82['y']}:shortest=0[base];"
    )


def _dual_video_81_slot(config: AppConfig) -> dict[str, int]:
    layout = config.pair_layout
    return {
        "x": layout.video81_x,
        "y": layout.video81_y,
        "w": layout.video81_width,
        "h": layout.video81_height,
    }


def _dual_video_82_slot(config: AppConfig) -> dict[str, int]:
    layout = config.pair_layout
    return {
        "x": layout.video82_x,
        "y": layout.video82_y,
        "w": layout.video82_width,
        "h": layout.video82_height,
    }


def _input_duration(input_info: Optional[VideoInfo] | list[VideoInfo] | tuple[VideoInfo, ...]) -> Optional[float]:
    if isinstance(input_info, (list, tuple)):
        durations = [info.duration for info in input_info if info.duration]
        return min(durations) if durations else None
    return input_info.duration if input_info and input_info.duration else None


def _duration_args(
    config: AppConfig,
    input_info: Optional[VideoInfo] | list[VideoInfo] | tuple[VideoInfo, ...],
    preset_info: Optional[VideoInfo],
) -> List[str]:
    mode = config.export.duration_mode
    max_dur = config.export.max_duration_seconds
    duration: Optional[float] = None
    input_duration = _input_duration(input_info)
    if mode == "input" and input_duration:
        duration = min(input_duration, max_dur)
    elif mode == "preset" and preset_info and preset_info.duration:
        duration = min(preset_info.duration, max_dur)
    else:
        duration = float(max_dur)
    return ["-t", f"{duration:.3f}".rstrip("0").rstrip(".")]


def decide_preset_type(config: AppConfig, preset_info: Optional[VideoInfo]) -> str:
    if config.export.preset_type in ("alpha", "green"):
        return config.export.preset_type
    if preset_info and preset_info.has_alpha:
        return "alpha"
    return "green"


def _bool_to_ffmpeg(value: Any) -> str:
    return "1" if bool(value) else "0"


def _green_key_filter(config: AppConfig, preset_info: Optional[VideoInfo]) -> str:
    """
    Remove fundo verde do preset.

    `colorkey` é o padrão de produção. Além de preservar as bordas em RGB, ele
    também foi mais de duas vezes mais rápido que `chromakey` com os presets
    ProRes 422 LT/yuv422p10le usados em produção. `chromakey` continua disponível
    quando for solicitado explicitamente no settings.json.
    """
    ck = config.export.chromakey
    method = getattr(ck, "method", "auto") or "auto"
    if method == "auto":
        method = "colorkey"

    if method == "colorkey":
        chain = f"format=rgba,colorkey={ck.color}:{ck.similarity}:{ck.blend}"
    else:
        chain = f"chromakey={ck.color}:{ck.similarity}:{ck.blend}"

    if getattr(ck, "despill", False):
        chain += (
            f",despill=type=green"
            f":mix={ck.despill_mix}"
            f":expand={ck.despill_expand}"
            f":green={ck.despill_green}"
            f":alpha={_bool_to_ffmpeg(ck.despill_alpha)}"
        )
    return chain


def _video_encoder_args(codec: str, mode: Optional[RenderMode], config: AppConfig) -> List[str]:
    profile = get_profile(mode or config.mode)

    # Modo PDV: tenta chegar perto do tamanho do ultra, mas usando encoder mais cuidadoso.
    # A ideia não é mágica: o bitrate continua em 4M para manter ~33 MB em vídeos de ~65s,
    # mas o encoder de GPU trabalha com preset mais cuidadoso para gastar melhor os bits nas áreas úteis.
    if profile.name == "pdv":
        if codec == "h264_nvenc":
            return [
                "-c:v", "h264_nvenc",
                "-preset", profile.nvenc_preset,
                "-rc", "vbr",
                "-b:v", profile.bitrate,
                "-maxrate", "5M",
                "-bufsize", "8M",
                "-spatial-aq", "1",
                "-aq-strength", "8",
                "-temporal-aq", "1",
            ]
        if codec == "h264_amf":
            return [
                "-c:v", "h264_amf",
                "-quality", profile.amf_quality,
                "-rc", "vbr_peak",
                "-b:v", profile.bitrate,
                "-maxrate", "5M",
                "-bufsize", "8M",
            ]
        if codec == "libx264":
            # CPU: usa preset mais cuidadoso que ultra, mas ainda viável para teste local.
            return ["-c:v", "libx264", "-preset", profile.x264_preset, "-b:v", profile.bitrate]

    if codec == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", profile.nvenc_preset, "-b:v", profile.bitrate]
    if codec == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", profile.amf_quality, "-b:v", profile.bitrate]
    if codec == "libx264":
        # CPU: sacrifica velocidade, mas permite validar filtro/chromakey/preset em máquinas sem NVIDIA.
        return ["-c:v", "libx264", "-preset", profile.x264_preset, "-b:v", profile.bitrate]
    raise FFmpegBuildError(f"Codec de vídeo inválido: {codec}")


def _audio_encoder_args(config: AppConfig) -> List[str]:
    """Monta os argumentos de áudio.

    Padrão recomendado para GoPro: `copy`.
    Como os arquivos de entrada já chegam em AAC 48 kHz estéreo, copiar o áudio evita
    recompressão e preserva exatamente o áudio original captado pela câmera.
    """
    audio_codec = str(config.export.audio_codec or "aac").strip().lower()
    if audio_codec in ("copy", "original", "orig", "copiar"):
        return ["-c:a", "copy"]

    args = ["-c:a", config.export.audio_codec]
    if config.export.audio_bitrate:
        args.extend(["-b:a", config.export.audio_bitrate])
    if config.export.audio_sample_rate:
        args.extend(["-ar", str(config.export.audio_sample_rate)])
    if config.export.audio_channels:
        args.extend(["-ac", str(config.export.audio_channels)])
    return args


def build_ffmpeg_command(
    input_path: Path,
    preset_path: Path,
    output_path: Path,
    config: AppConfig,
    mode: Optional[RenderMode] = None,
    input_info: Optional[VideoInfo] = None,
    preset_info: Optional[VideoInfo] = None,
    force_codec: Optional[VideoCodec] = None,
) -> List[str]:
    ffmpeg = require_ffmpeg()
    requested_codec = force_codec or config.export.codec
    codec, _ = choose_video_codec(requested_codec)
    preset_type = decide_preset_type(config, preset_info)
    base_graph = _base_graph_segment(config)

    if preset_type == "green":
        key_filter = _green_key_filter(config, preset_info)
        filter_complex = (
            base_graph
            + f"[1:v]{key_filter},setpts=PTS-STARTPTS[preset];"
            + f"[base][preset]overlay=0:0:shortest=1,format=yuv420p[outv]"
        )
    else:
        filter_complex = (
            base_graph
            + f"[1:v]format=rgba,setpts=PTS-STARTPTS[preset];"
            + f"[base][preset]overlay=0:0:shortest=1,format=yuv420p[outv]"
        )

    # build_output_path() já escolhe um nome livre antes de chamar o FFmpeg.
    # -n mantém uma segunda trava: se outro processo criar esse mesmo arquivo entre
    # a escolha do nome e a execução, o FFmpeg falha em vez de sobrescrever.
    cmd: List[str] = [
        ffmpeg,
        "-n",
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel", "warning",
        "-i", str(input_path),
        "-i", str(preset_path),
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "0:a?",
        *_duration_args(config, input_info, preset_info),
        *_video_encoder_args(codec, mode, config),
        "-pix_fmt", "yuv420p",
        *_audio_encoder_args(config),
        "-movflags", "+faststart",
        str(output_path),
    ]
    return cmd


def build_ffmpeg_pair_command(
    input_81_path: Path,
    input_82_path: Path,
    preset_path: Path,
    output_path: Path,
    config: AppConfig,
    mode: Optional[RenderMode] = None,
    input_81_info: Optional[VideoInfo] = None,
    input_82_info: Optional[VideoInfo] = None,
    preset_info: Optional[VideoInfo] = None,
    force_codec: Optional[VideoCodec] = None,
) -> List[str]:
    ffmpeg = require_ffmpeg()
    requested_codec = force_codec or config.export.codec
    codec, _ = choose_video_codec(requested_codec)
    preset_type = decide_preset_type(config, preset_info)
    base_graph = _dual_base_graph_segment(config)
    layout = config.pair_layout
    video_81 = _dual_video_81_slot(config)
    video_81_start = _ffmpeg_number(layout.video81_start_seconds)
    video_81_end = _ffmpeg_number(layout.video81_end_seconds)
    preset_input_index = 2
    audio_tail = ""
    audio_map = "1:a?"

    if layout.video82_start_seconds > 0 and input_82_info and input_82_info.has_audio:
        delay_ms = max(0, int(round(layout.video82_start_seconds * 1000)))
        audio_tail = f"[1:a]adelay={delay_ms}|{delay_ms}[aout];"
        audio_map = "[aout]"

    if preset_type == "green":
        key_filter = _green_key_filter(config, preset_info)
        filter_complex = (
            base_graph
            + f"[{preset_input_index}:v]{key_filter},setpts=PTS-STARTPTS[preset];"
            + f"[base][preset]overlay=0:0:shortest=1[withpreset];"
            + f"[withpreset][video81]overlay={video_81['x']}:{video_81['y']}:shortest=0:eof_action=pass:"
            + f"enable='between(t,{video_81_start},{video_81_end})',format=yuv420p[outv];"
            + audio_tail
        )
    else:
        filter_complex = (
            base_graph
            + f"[{preset_input_index}:v]format=rgba,setpts=PTS-STARTPTS[preset];"
            + f"[base][preset]overlay=0:0:shortest=1[withpreset];"
            + f"[withpreset][video81]overlay={video_81['x']}:{video_81['y']}:shortest=0:eof_action=pass:"
            + f"enable='between(t,{video_81_start},{video_81_end})',format=yuv420p[outv];"
            + audio_tail
        )

    input_82_seek = _input_seek_before_args(layout.video82_trim_start_seconds)
    input_infos = [info for info in (input_81_info, input_82_info) if info]
    cmd: List[str] = [
        ffmpeg,
        "-n",
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel", "warning",
        "-i", str(input_81_path),
        *input_82_seek,
        "-i", str(input_82_path),
        "-i", str(preset_path),
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", audio_map,
        *_duration_args(config, input_infos, preset_info),
        *_video_encoder_args(codec, mode, config),
        "-pix_fmt", "yuv420p",
        *_audio_encoder_args(config),
        "-movflags", "+faststart",
        str(output_path),
    ]
    return cmd


def command_to_string(cmd: List[str]) -> str:
    def quote(part: str) -> str:
        if any(ch in part for ch in [' ', '&', '(', ')', '[', ']', ';', ':']) and not part.startswith('"'):
            return f'"{part}"'
        return part
    return " ".join(quote(str(p)) for p in cmd)
