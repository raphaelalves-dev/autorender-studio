from __future__ import annotations

import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.config import AppConfig, DEFAULT_SPECIAL_CYCLE_FOLDERS, RenderMode, VideoCodec, ensure_directories
from backend.ffmpeg_builder import (
    build_ffmpeg_command,
    build_ffmpeg_pair_command,
    build_output_path,
    choose_video_codec,
    command_to_string,
    ffmpeg_supports_libx264,
    ffmpeg_supports_nvenc,
    release_reserved_output_path,
    test_amf_runtime,
    test_nvenc_runtime,
)
from backend.ffprobe_reader import VideoInfo, probe_media
from backend.logger import append_jsonl, setup_logger
from backend.delivery import local_render_path, queue_delivery, resume_pending_deliveries
from backend.history import RenderHistory
from backend.subprocess_utils import hidden_console_kwargs
from backend.input_selector import (
    cycle_folder_for_path,
    count_cycle_videos,
    remaining_unprocessed_in_cycle,
    source_mode_for_path,
)


@dataclass
class RenderResult:
    success: bool
    input_file: str
    output_file: str
    started_at: str
    ended_at: str
    elapsed_seconds: float
    input_size_bytes: Optional[int]
    output_size_bytes: Optional[int]
    return_code: Optional[int]
    command: str
    error: Optional[str]
    input_info: Optional[dict]
    output_info: Optional[dict]
    preset_info: Optional[dict]
    delivery_pending: bool = False
    local_output_file: Optional[str] = None


@dataclass
class BothRenderResult:
    pair_result: RenderResult
    single_result: RenderResult
    elapsed_seconds: float


class RenderError(RuntimeError):
    pass


_MOVE_LOCK = threading.Lock()


def _compact_media_info(value: Optional[dict]) -> Optional[dict]:
    """Mantém métricas úteis no JSONL sem repetir todos os streams do ffprobe."""
    if not value:
        return value
    compact = {key: item for key, item in value.items() if key != "streams"}
    videos = compact.get("videos")
    if isinstance(videos, list):
        compact["videos"] = [
            {key: item for key, item in video.items() if key != "streams"}
            if isinstance(video, dict) else video
            for video in videos
        ]
    return compact


def _result_log_row(result: RenderResult) -> dict:
    row = asdict(result)
    for key in ("input_info", "output_info", "preset_info"):
        row[key] = _compact_media_info(row.get(key))
    return row


def _timeout_seconds(value: int | float | None) -> int | None:
    seconds = int(value or 0)
    return seconds if seconds > 0 else None


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size if path.exists() else None
    except OSError:
        return None


def _cleanup_partial_output(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _run_ffmpeg_command(cmd: list[str], config: AppConfig) -> tuple[int, str]:
    timeout = _timeout_seconds(getattr(config, "ffmpeg_timeout_seconds", 1800))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **hidden_console_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        seconds = timeout or 0
        stderr = (exc.stderr or exc.stdout or "")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        detail = stderr.strip()
        message = f"FFmpeg excedeu o limite de {seconds}s e foi encerrado."
        raise RenderError(f"{message}\n{detail}" if detail else message) from exc

    stderr = proc.stderr or proc.stdout or ""
    return proc.returncode, stderr


def validate_environment(config: AppConfig) -> None:
    ensure_directories(config)
    if not config.preset_path.exists():
        raise RenderError(f"Preset .MOV não encontrado: {config.preset_path}")
    if not config.pair_preset_path.exists():
        raise RenderError(f"Preset duplo .MOV não encontrado: {config.pair_preset_path}")

    if config.export.codec == "h264_nvenc":
        ok, detail = test_nvenc_runtime()
        if not ok:
            raise RenderError(
                "NVENC foi exigido, mas não funcionou neste PC. Use codec auto/h264_amf/libx264 para continuar.\n"
                + detail
            )
    elif config.export.codec == "h264_amf":
        ok, detail = test_amf_runtime()
        if not ok:
            raise RenderError(
                "AMD AMF foi exigido, mas não funcionou neste PC. Use codec auto/libx264 para continuar.\n"
                + detail
            )
    elif config.export.codec == "libx264":
        ok, detail = ffmpeg_supports_libx264()
        if not ok:
            raise RenderError("FFmpeg encontrado, mas sem libx264. Baixe uma build completa do FFmpeg.")
    else:
        # auto: basta ter uma GPU compatível ou libx264 para fallback.
        try:
            choose_video_codec("auto")
        except Exception as exc:
            raise RenderError(str(exc)) from exc


def _archive_dir_for_input(src: Path, base_dir: Path, config: AppConfig) -> Path:
    if not config.preserve_cycle_folder_when_moving:
        return base_dir
    try:
        relative = src.resolve().relative_to(config.input_path.resolve())
    except ValueError:
        return base_dir
    if len(relative.parts) <= 1:
        return base_dir
    return base_dir / relative.parent


def _move_file_unlocked(src: Path, dst_dir: Path) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    target = dst_dir / src.name
    if not target.exists():
        return Path(shutil.move(str(src), str(target)))
    idx = 1
    while True:
        candidate = dst_dir / f"{src.stem}_{idx:03d}{src.suffix}"
        if not candidate.exists():
            return Path(shutil.move(str(src), str(candidate)))
        idx += 1


def _move_file(src: Path, dst_dir: Path) -> Path:
    with _MOVE_LOCK:
        return _move_file_unlocked(src, dst_dir)


def _move_files_transactionally(moves: list[tuple[Path, Path]]) -> list[Path]:
    """Move um grupo inteiro e devolve os arquivos anteriores se uma etapa falhar."""
    moved: list[tuple[Path, Path]] = []
    with _MOVE_LOCK:
        try:
            for source, destination_dir in moves:
                if not source.exists():
                    raise FileNotFoundError(f"Original não encontrado durante a finalização: {source}")
                moved_path = _move_file_unlocked(source, destination_dir)
                moved.append((source, moved_path))
        except Exception as exc:
            rollback_errors: list[str] = []
            for source, moved_path in reversed(moved):
                try:
                    if not moved_path.exists():
                        continue
                    source.parent.mkdir(parents=True, exist_ok=True)
                    if source.exists():
                        raise FileExistsError(f"o destino original já existe: {source}")
                    shutil.move(str(moved_path), str(source))
                except Exception as rollback_exc:
                    rollback_errors.append(f"{moved_path.name}: {rollback_exc}")

            detail = ""
            if rollback_errors:
                detail = " Falha ao devolver: " + "; ".join(rollback_errors)
            raise RenderError(f"Falha ao arquivar os originais; movimentação revertida.{detail} Causa: {exc}") from exc
    return [moved_path for _source, moved_path in moved]


def render_one(
    input_path: str | Path,
    config: AppConfig,
    mode: Optional[RenderMode] = None,
    move_original: Optional[bool] = None,
    codec: Optional[VideoCodec] = None,
    *,
    record_history: bool = True,
    move_on_error: Optional[bool] = None,
    render_started_at: Optional[datetime] = None,
) -> RenderResult:
    logger = setup_logger(config.logs_path)
    ensure_directories(config)
    resume_pending_deliveries(config)

    input_file = Path(input_path)
    if not input_file.exists():
        raise RenderError(f"Vídeo de entrada não encontrado: {input_file}")
    if not config.preset_path.exists():
        raise RenderError(f"Preset .MOV não encontrado: {config.preset_path}")

    started = render_started_at or datetime.now()
    output_file = build_output_path(
        input_file,
        config.output_path,
        config.preset_path,
        input_root=config.input_path,
        group_by_cycle_folder=config.group_output_by_cycle_folder,
        loose_output_folder_by_render_time=config.loose_output_folder_by_render_time,
        loose_output_time_format=config.loose_output_time_format,
        render_started_at=started,
        special_cycle_folders=getattr(config, "special_cycle_folders", DEFAULT_SPECIAL_CYCLE_FOLDERS),
    )
    render_file = local_render_path(output_file, config)
    staged_output = render_file != output_file
    started_ts = started.isoformat(timespec="seconds")
    start_perf = time.perf_counter()

    input_info: Optional[VideoInfo] = None
    preset_info: Optional[VideoInfo] = None
    output_info: Optional[VideoInfo] = None
    cmd = []
    stderr = ""
    return_code: Optional[int] = None
    output_size_bytes: Optional[int] = None
    delivery_pending = False

    try:
        input_info = probe_media(input_file, config.ffprobe_timeout_seconds)
        preset_info = probe_media(config.preset_path, config.ffprobe_timeout_seconds)
        cmd = build_ffmpeg_command(
            input_file,
            config.preset_path,
            render_file,
            config,
            mode=mode,
            input_info=input_info,
            preset_info=preset_info,
            force_codec=codec,
        )
        logger.info("Renderizando %s -> %s", input_file.name, output_file.name)
        if staged_output:
            logger.info("Render local ativo; entrega final será feita para %s", output_file)
        logger.info("Comando FFmpeg: %s", command_to_string(cmd))
        return_code, stderr = _run_ffmpeg_command(cmd, config)
        if return_code != 0:
            raise RenderError(stderr.strip() or f"FFmpeg retornou código {return_code}")
        if not render_file.exists() or render_file.stat().st_size == 0:
            raise RenderError("FFmpeg terminou sem gerar um arquivo de saída válido.")
        output_size_bytes = _file_size(render_file)
        output_info = probe_media(render_file, config.ffprobe_timeout_seconds)
        success = True
        error = None
        elapsed_now = time.perf_counter() - start_perf
        logger.info("Finalizado: %s em %.2fs", output_file.name, elapsed_now)
        if staged_output:
            queue_delivery(render_file, output_file, config)
            delivery_pending = True
            logger.info("Entrega para a saída adicionada à fila: %s", output_file)

        history = RenderHistory(config.history_path, config.input_path) if config.history_enabled and record_history else None
        cycle_folder = cycle_folder_for_path(input_file, config)
        source_stat = input_file.stat()
        source_mode = source_mode_for_path(input_file, config)
        cycle_video_count_before_move = (
            count_cycle_videos(cycle_folder, recursive=config.recursive_input)
            if cycle_folder and cycle_folder.exists()
            else 0
        )

        should_move = config.move_original_on_success if move_original is None else move_original
        if should_move:
            _move_files_transactionally(
                [(input_file, _archive_dir_for_input(input_file, config.processed_path, config))]
            )

        if history:
            try:
                history.mark_video_success(
                    input_file,
                    output_file,
                    source_mode=source_mode,
                    cycle_folder=cycle_folder,
                    preset_name=config.preset_path.stem,
                    elapsed_seconds=round(elapsed_now, 3),
                    size_bytes=source_stat.st_size,
                    mtime_ns=source_stat.st_mtime_ns,
                )
                if cycle_folder:
                    remaining = (
                        remaining_unprocessed_in_cycle(cycle_folder, history, recursive=config.recursive_input)
                        if cycle_folder.exists()
                        else []
                    )
                    if not remaining:
                        history.mark_cycle_complete(
                            cycle_folder,
                            video_count=cycle_video_count_before_move,
                        )
            except Exception as history_exc:
                logger.warning("Render concluído, mas o histórico não foi atualizado: %s", history_exc)
    except Exception as exc:
        success = False
        error = str(exc)
        _cleanup_partial_output(render_file)
        logger.error("Falha ao processar %s: %s", input_file.name, error)
        should_move_error = config.move_original_on_error if move_on_error is None else move_on_error
        if should_move_error and input_file.exists():
            _move_file(input_file, _archive_dir_for_input(input_file, config.error_path, config))
    finally:
        release_reserved_output_path(output_file)
    ended = datetime.now()
    elapsed = round(time.perf_counter() - start_perf, 3)

    result = RenderResult(
        success=success,
        input_file=str(input_file),
        output_file=str(output_file),
        started_at=started_ts,
        ended_at=ended.isoformat(timespec="seconds"),
        elapsed_seconds=elapsed,
        input_size_bytes=_file_size(input_file),
        output_size_bytes=output_size_bytes or _file_size(output_file),
        return_code=return_code,
        command=command_to_string(cmd) if cmd else "",
        error=error,
        input_info=input_info.to_dict() if input_info else None,
        output_info=output_info.to_dict() if output_info else None,
        preset_info=preset_info.to_dict() if preset_info else None,
        delivery_pending=delivery_pending,
        local_output_file=str(render_file) if staged_output and delivery_pending else None,
    )
    append_jsonl(config.logs_path, "renders.jsonl", _result_log_row(result))
    if not success:
        raise RenderError(error or "Falha desconhecida")
    return result


def render_pair(
    input_81_path: str | Path,
    input_82_path: str | Path,
    config: AppConfig,
    mode: Optional[RenderMode] = None,
    move_original: Optional[bool] = None,
    codec: Optional[VideoCodec] = None,
    *,
    record_history: bool = True,
    move_on_error: Optional[bool] = None,
    render_started_at: Optional[datetime] = None,
) -> RenderResult:
    logger = setup_logger(config.logs_path)
    ensure_directories(config)
    resume_pending_deliveries(config)

    input_81_file = Path(input_81_path)
    input_82_file = Path(input_82_path)
    input_files = [input_81_file, input_82_file]
    for input_file in input_files:
        if not input_file.exists():
            raise RenderError(f"Vídeo de entrada não encontrado: {input_file}")
    pair_preset_path = config.pair_preset_path
    if not pair_preset_path.exists():
        raise RenderError(f"Preset duplo .MOV não encontrado: {pair_preset_path}")

    started = render_started_at or datetime.now()
    output_file = build_output_path(
        input_81_file,
        config.output_path,
        pair_preset_path,
        input_root=config.input_path,
        group_by_cycle_folder=config.group_output_by_cycle_folder,
        loose_output_folder_by_render_time=config.loose_output_folder_by_render_time,
        loose_output_time_format=config.loose_output_time_format,
        render_started_at=started,
        special_cycle_folders=getattr(config, "special_cycle_folders", DEFAULT_SPECIAL_CYCLE_FOLDERS),
        output_stem=f"{input_81_file.stem}_{input_82_file.stem}",
    )
    render_file = local_render_path(output_file, config)
    staged_output = render_file != output_file
    started_ts = started.isoformat(timespec="seconds")
    start_perf = time.perf_counter()

    input_81_info: Optional[VideoInfo] = None
    input_82_info: Optional[VideoInfo] = None
    preset_info: Optional[VideoInfo] = None
    output_info: Optional[VideoInfo] = None
    input_size_bytes = sum(input_file.stat().st_size for input_file in input_files if input_file.exists())
    cmd = []
    stderr = ""
    return_code: Optional[int] = None
    output_size_bytes: Optional[int] = None
    delivery_pending = False

    try:
        input_81_info = probe_media(input_81_file, config.ffprobe_timeout_seconds)
        input_82_info = probe_media(input_82_file, config.ffprobe_timeout_seconds)
        preset_info = probe_media(pair_preset_path, config.ffprobe_timeout_seconds)
        cmd = build_ffmpeg_pair_command(
            input_81_file,
            input_82_file,
            pair_preset_path,
            render_file,
            config,
            mode=mode,
            input_81_info=input_81_info,
            input_82_info=input_82_info,
            preset_info=preset_info,
            force_codec=codec,
        )
        logger.info("Renderizando par %s + %s -> %s", input_81_file.name, input_82_file.name, output_file.name)
        if staged_output:
            logger.info("Render local ativo; entrega final será feita para %s", output_file)
        logger.info("Comando FFmpeg: %s", command_to_string(cmd))
        return_code, stderr = _run_ffmpeg_command(cmd, config)
        if return_code != 0:
            raise RenderError(stderr.strip() or f"FFmpeg retornou código {return_code}")
        if not render_file.exists() or render_file.stat().st_size == 0:
            raise RenderError("FFmpeg terminou sem gerar um arquivo de saída válido.")
        output_size_bytes = _file_size(render_file)
        output_info = probe_media(render_file, config.ffprobe_timeout_seconds)
        success = True
        error = None
        elapsed_now = time.perf_counter() - start_perf
        logger.info("Finalizado: %s em %.2fs", output_file.name, elapsed_now)
        if staged_output:
            queue_delivery(render_file, output_file, config)
            delivery_pending = True
            logger.info("Entrega para a saída adicionada à fila: %s", output_file)

        history = RenderHistory(config.history_path, config.input_path) if config.history_enabled and record_history else None
        cycle_folder = cycle_folder_for_path(input_81_file, config)
        source_snapshots = []
        for input_file in input_files:
            source_stat = input_file.stat()
            source_snapshots.append(
                (
                    input_file,
                    source_stat.st_size,
                    source_stat.st_mtime_ns,
                    source_mode_for_path(input_file, config),
                    cycle_folder_for_path(input_file, config),
                )
            )
        cycle_video_count_before_move = (
            count_cycle_videos(cycle_folder, recursive=config.recursive_input)
            if cycle_folder and cycle_folder.exists()
            else 0
        )

        should_move = config.move_original_on_success if move_original is None else move_original
        if should_move:
            _move_files_transactionally(
                [
                    (input_file, _archive_dir_for_input(input_file, config.processed_path, config))
                    for input_file in input_files
                ]
            )

        if history:
            try:
                for source, size_bytes, mtime_ns, source_mode, source_cycle in source_snapshots:
                    history.mark_video_success(
                        source,
                        output_file,
                        source_mode=source_mode,
                        cycle_folder=source_cycle,
                        preset_name=pair_preset_path.stem,
                        elapsed_seconds=round(elapsed_now, 3),
                        size_bytes=size_bytes,
                        mtime_ns=mtime_ns,
                    )
                if cycle_folder:
                    remaining = (
                        remaining_unprocessed_in_cycle(cycle_folder, history, recursive=config.recursive_input)
                        if cycle_folder.exists()
                        else []
                    )
                    if not remaining:
                        history.mark_cycle_complete(
                            cycle_folder,
                            video_count=cycle_video_count_before_move,
                        )
            except Exception as history_exc:
                logger.warning("Render concluído, mas o histórico do par não foi atualizado: %s", history_exc)
    except Exception as exc:
        success = False
        error = str(exc)
        _cleanup_partial_output(render_file)
        logger.error("Falha ao processar par %s + %s: %s", input_81_file.name, input_82_file.name, error)
        should_move_error = config.move_original_on_error if move_on_error is None else move_on_error
        if should_move_error:
            for input_file in input_files:
                if input_file.exists():
                    _move_file(input_file, _archive_dir_for_input(input_file, config.error_path, config))
    finally:
        release_reserved_output_path(output_file)
    ended = datetime.now()
    elapsed = round(time.perf_counter() - start_perf, 3)

    result = RenderResult(
        success=success,
        input_file=" + ".join(str(input_file) for input_file in input_files),
        output_file=str(output_file),
        started_at=started_ts,
        ended_at=ended.isoformat(timespec="seconds"),
        elapsed_seconds=elapsed,
        input_size_bytes=input_size_bytes,
        output_size_bytes=output_size_bytes or _file_size(output_file),
        return_code=return_code,
        command=command_to_string(cmd) if cmd else "",
        error=error,
        input_info={
            "videos": [
                input_81_info.to_dict() if input_81_info else None,
                input_82_info.to_dict() if input_82_info else None,
            ]
        },
        output_info=output_info.to_dict() if output_info else None,
        preset_info=preset_info.to_dict() if preset_info else None,
        delivery_pending=delivery_pending,
        local_output_file=str(render_file) if staged_output and delivery_pending else None,
    )
    append_jsonl(config.logs_path, "renders.jsonl", _result_log_row(result))
    if not success:
        raise RenderError(error or "Falha desconhecida")
    return result


def render_both(
    input_81_path: str | Path,
    input_82_path: str | Path,
    config: AppConfig,
    mode: Optional[RenderMode] = None,
    move_original: Optional[bool] = None,
    codec: Optional[VideoCodec] = None,
) -> BothRenderResult:
    """Gera os formatos de duas câmeras e de uma câmera antes de mover os originais."""
    logger = setup_logger(config.logs_path)
    input_81_file = Path(input_81_path)
    input_82_file = Path(input_82_path)
    input_files = [input_81_file, input_82_file]
    started = datetime.now()
    pair_result: RenderResult | None = None

    try:
        pair_result = render_pair(
            input_81_file,
            input_82_file,
            config,
            mode=mode,
            move_original=False,
            codec=codec,
            record_history=False,
            move_on_error=False,
            render_started_at=started,
        )
        single_result = render_one(
            input_82_file,
            config,
            mode=mode,
            move_original=False,
            codec=codec,
            record_history=False,
            move_on_error=False,
            render_started_at=started,
        )
    except Exception:
        if pair_result:
            _cleanup_partial_output(Path(pair_result.output_file))
        raise

    history = RenderHistory(config.history_path, config.input_path) if config.history_enabled else None
    cycle_folder = cycle_folder_for_path(input_81_file, config)
    source_snapshots = []
    for input_file in input_files:
        source_stat = input_file.stat()
        source_snapshots.append(
            (
                input_file,
                source_stat.st_size,
                source_stat.st_mtime_ns,
                cycle_folder_for_path(input_file, config),
            )
        )
    cycle_video_count_before_move = (
        count_cycle_videos(cycle_folder, recursive=config.recursive_input)
        if history and cycle_folder and cycle_folder.exists()
        else 0
    )

    try:
        should_move = config.move_original_on_success if move_original is None else move_original
        if should_move:
            _move_files_transactionally(
                [
                    (input_file, _archive_dir_for_input(input_file, config.processed_path, config))
                    for input_file in input_files
                ]
            )
    except Exception as exc:
        _cleanup_partial_output(Path(pair_result.output_file))
        _cleanup_partial_output(Path(single_result.output_file))
        raise RenderError(f"Os dois formatos foram gerados, mas a finalização falhou: {exc}") from exc

    if history:
        try:
            pair_snapshot, single_snapshot = source_snapshots
            history.mark_video_success(
                pair_snapshot[0],
                pair_result.output_file,
                source_mode="both",
                cycle_folder=pair_snapshot[3],
                preset_name=config.pair_preset_path.stem,
                elapsed_seconds=pair_result.elapsed_seconds,
                size_bytes=pair_snapshot[1],
                mtime_ns=pair_snapshot[2],
            )
            history.mark_video_success(
                single_snapshot[0],
                single_result.output_file,
                source_mode="both",
                cycle_folder=single_snapshot[3],
                preset_name=config.preset_path.stem,
                elapsed_seconds=single_result.elapsed_seconds,
                size_bytes=single_snapshot[1],
                mtime_ns=single_snapshot[2],
            )
            if cycle_folder:
                remaining = (
                    remaining_unprocessed_in_cycle(cycle_folder, history, recursive=config.recursive_input)
                    if cycle_folder.exists()
                    else []
                )
                if not remaining:
                    history.mark_cycle_complete(cycle_folder, video_count=cycle_video_count_before_move)
        except Exception as history_exc:
            logger.warning("Os dois formatos foram concluídos, mas o histórico não foi atualizado: %s", history_exc)

    return BothRenderResult(
        pair_result=pair_result,
        single_result=single_result,
        elapsed_seconds=round(pair_result.elapsed_seconds + single_result.elapsed_seconds, 3),
    )
