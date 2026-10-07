from __future__ import annotations

import re
import subprocess
import math
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from backend.config import AppConfig
from backend.delivery import local_render_path, queue_delivery, should_stage_output, staging_root
from backend.ffmpeg_builder import require_ffmpeg
from backend.ffprobe_reader import probe_media
from backend.logger import setup_logger
from backend.subprocess_utils import hidden_console_kwargs


@dataclass
class PhotoReport:
    created: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def camera_name(source: Path) -> str:
    match = re.match(r"^(8[1-6])(?=[_-]|$)", source.stem, re.IGNORECASE)
    return match.group(1) if match else "SEM_ID"


def photo_times(config: AppConfig) -> list[tuple[int, int, int]]:
    centers = config.photo_center_seconds
    if len(centers) != config.photo_group_count:
        raise ValueError("A quantidade de grupos deve corresponder à lista de segundos centrais.")
    if centers == [0]:
        return []
    if len(set(centers)) != len(centers) or any(center < 1 for center in centers):
        raise ValueError("Os segundos centrais devem ser únicos e maiores ou iguais a 1.")
    return [(center - 1, center, center + 1) for center in centers]


def _extract_every_second(source: Path, output_video: Path, duration: float, config: AppConfig,
                          ffmpeg: str, report: PhotoReport, logger) -> None:
    """Decodifica o original uma vez e extrai um JPG para cada segundo disponível."""
    destination = output_video.parent / "fotos" / camera_name(source)
    local_root = staging_root(config) if should_stage_output(destination, config) else destination
    local_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".autorender_photos_", dir=local_root) as folder:
        pattern = Path(folder) / "%06d.jpg"
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(source), "-map", "0:v:0", "-vf", "fps=1:start_time=0:eof_action=pass",
            "-frames:v", str(max(1, math.ceil(duration))), "-q:v", "2",
            "-start_number", "0", "-f", "image2", str(pattern),
        ]
        timeout = config.ffmpeg_timeout_seconds or None
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                   **hidden_console_kwargs())
        frames = sorted(Path(folder).glob("*.jpg"))
        if completed.returncode != 0 or not frames:
            raise RuntimeError(completed.stderr.strip() or "nenhum quadro extraído do original")
        for frame in frames:
            second = int(frame.stem)
            final = destination / f"{output_video.stem}__{camera_name(source)}__{source.stem}__{second:04d}s.jpg"
            local = local_render_path(final, config)
            local.parent.mkdir(parents=True, exist_ok=True)
            frame.replace(local)
            if local != final:
                queue_delivery(local, final, config)
            report.created.append(final)
        logger.info("Fotos do original %s: %s imagem(ns), uma por segundo", source.name, len(frames))


def extract_photos(sources: list[Path], output_video: Path, config: AppConfig) -> PhotoReport:
    """Extrai fotos dos originais. Uma falha aqui nunca altera o resultado do render."""
    report = PhotoReport()
    if not config.photo_enabled:
        return report
    logger = setup_logger(config.logs_path)
    try:
        groups = photo_times(config)
        whole_video = config.photo_center_seconds == [0]
        ffmpeg = require_ffmpeg()
    except Exception as exc:
        report.warnings.append(str(exc))
        logger.warning("Fotos não geradas: %s", exc)
        return report

    for source in sources:
        try:
            info = probe_media(source, config.ffprobe_timeout_seconds)
            if info.duration is None:
                raise ValueError("duração do original indisponível")
            if whole_video:
                _extract_every_second(source, output_video, info.duration, config, ffmpeg, report, logger)
                continue
            destination = output_video.parent / "fotos"
            for before, center, after in groups:
                if after >= info.duration:
                    warning = f"{source.name}: trio {before}/{center}/{after}s fora da duração ({info.duration:.2f}s)"
                    report.warnings.append(warning)
                    logger.warning("Fotos ignoradas: %s", warning)
                    continue
                staged: list[tuple[Path, Path, Path]] = []
                try:
                    for second in (before, center, after):
                        final = destination / f"{output_video.stem}__{camera_name(source)}__{source.stem}__{second:04d}s.jpg"
                        local = local_render_path(final, config)
                        local.parent.mkdir(parents=True, exist_ok=True)
                        temporary = local.with_name(f".{uuid.uuid4().hex}_{local.name}")
                        command = [
                            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                            "-ss", str(second), "-i", str(source), "-map", "0:v:0",
                            "-frames:v", "1", "-q:v", "2", "-f", "image2", str(temporary),
                        ]
                        completed = subprocess.run(
                            command, capture_output=True, text=True, timeout=60,
                            **hidden_console_kwargs(),
                        )
                        if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
                            raise RuntimeError(completed.stderr.strip() or f"nenhum quadro no segundo {second}")
                        staged.append((temporary, local, final))
                    for temporary, local, final in staged:
                        temporary.replace(local)
                        if local != final:
                            queue_delivery(local, final, config)
                        report.created.append(final)
                    logger.info("Fotos do original %s: %s, %s e %s segundos", source.name, before, center, after)
                except Exception as exc:
                    for temporary, _local, _final in staged:
                        temporary.unlink(missing_ok=True)
                    warning = f"{source.name}, trio central em {center}s: {exc}"
                    report.warnings.append(warning)
                    logger.warning("Falha ao extrair fotos: %s", warning)
        except Exception as exc:
            warning = f"{source.name}: {exc}"
            report.warnings.append(warning)
            logger.warning("Falha ao preparar fotos: %s", warning)
    return report
