from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, asdict
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.subprocess_utils import hidden_console_kwargs


class ProbeError(RuntimeError):
    pass


@dataclass
class VideoInfo:
    path: str
    duration: Optional[float]
    width: Optional[int]
    height: Optional[int]
    fps: Optional[float]
    video_codec: Optional[str]
    video_pix_fmt: Optional[str]
    bitrate: Optional[int]
    has_audio: bool
    audio_codec: Optional[str]
    rotation: Optional[int]
    streams: List[Dict[str, Any]]

    @property
    def has_alpha(self) -> bool:
        pix_fmt = (self.video_pix_fmt or "").lower()
        return any(token in pix_fmt for token in ["yuva", "rgba", "argb", "bgra", "abgr"])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _find_executable(name: str, env_var: str) -> str | None:
    """Procura executáveis primeiro no projeto, depois no PATH."""
    import os

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
def require_ffprobe() -> str:
    executable = _find_executable("ffprobe.exe", "AUTORENDER_FFPROBE") or _find_executable("ffprobe", "AUTORENDER_FFPROBE")
    if not executable:
        raise ProbeError(
            "FFprobe não encontrado. Coloque ffprobe.exe em bin\\ ou adicione o FFmpeg ao PATH."
        )
    return executable


def _parse_fps(value: str | None) -> Optional[float]:
    if not value or value == "0/0":
        return None
    try:
        return float(Fraction(value))
    except Exception:
        return None


def _stream_rotation(stream: Dict[str, Any]) -> Optional[int]:
    tags = stream.get("tags") or {}
    side_data = stream.get("side_data_list") or []
    rotate = tags.get("rotate")
    if rotate is not None:
        try:
            return int(float(rotate))
        except ValueError:
            pass
    for item in side_data:
        if "rotation" in item:
            try:
                return int(float(item["rotation"]))
            except ValueError:
                return None
    return None


@lru_cache(maxsize=256)
def _probe_media_cached(media_path_key: str, _size_bytes: int, _mtime_ns: int, timeout_seconds: int) -> VideoInfo:
    ffprobe = require_ffprobe()
    media_path = Path(media_path_key)
    if not media_path.exists():
        raise ProbeError(f"Arquivo não encontrado: {media_path}")

    cmd = [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(media_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            **hidden_console_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"FFprobe demorou demais para ler {media_path.name} ({timeout_seconds}s).") from exc
    if proc.returncode != 0:
        raise ProbeError(f"FFprobe falhou para {media_path.name}: {proc.stderr.strip()}")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"Resposta inválida do FFprobe: {exc}") from exc

    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}

    duration = video_stream.get("duration") if video_stream else None
    if duration is None:
        duration = fmt.get("duration")
    duration_float = float(duration) if duration not in (None, "N/A") else None

    bitrate = fmt.get("bit_rate") or (video_stream or {}).get("bit_rate")
    bitrate_int = int(bitrate) if bitrate not in (None, "N/A") else None

    info = VideoInfo(
        path=str(media_path),
        duration=duration_float,
        width=int(video_stream["width"]) if video_stream and video_stream.get("width") else None,
        height=int(video_stream["height"]) if video_stream and video_stream.get("height") else None,
        fps=_parse_fps((video_stream or {}).get("avg_frame_rate") or (video_stream or {}).get("r_frame_rate")),
        video_codec=(video_stream or {}).get("codec_name"),
        video_pix_fmt=(video_stream or {}).get("pix_fmt"),
        bitrate=bitrate_int,
        has_audio=audio_stream is not None,
        audio_codec=(audio_stream or {}).get("codec_name") if audio_stream else None,
        rotation=_stream_rotation(video_stream or {}),
        streams=streams,
    )
    if info.width is None or info.height is None:
        raise ProbeError(f"Vídeo sem stream de vídeo válido: {media_path.name}")
    return info


def probe_media(path: str | Path, timeout_seconds: int = 30) -> VideoInfo:
    media_path = Path(path)
    timeout_seconds = max(1, int(timeout_seconds or 30))
    try:
        stat = media_path.stat()
    except FileNotFoundError as exc:
        raise ProbeError(f"Arquivo não encontrado: {media_path}") from exc
    except OSError as exc:
        raise ProbeError(f"Não foi possível ler arquivo: {media_path}: {exc}") from exc

    return _probe_media_cached(str(media_path.resolve()), stat.st_size, stat.st_mtime_ns, timeout_seconds)
