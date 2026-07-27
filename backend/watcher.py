from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from backend.config import AppConfig, clamp_parallel_workers, effective_render_format
from backend.ffprobe_reader import ProbeError, probe_media
from backend.history import RenderHistory
from backend.input_selector import (
    CandidateVideoGroup,
    all_input_videos,
    candidate_all_videos,
    candidate_manual_82_videos,
    candidate_video_groups,
    cycle_folder_for_path,
)
from backend.queue_manager import is_video_file, wait_for_stable_file
from backend.quarantine import quarantine_files
from backend.renderer import RenderError, render_both, render_one, render_pair
from backend.delivery import defer_deliveries_until_batch_complete


class WatcherError(RuntimeError):
    pass


_PROCESS_LOCK = threading.Lock()
_MEDIA_RETRY_LOCK = threading.Lock()
RenderJob = TypeVar("RenderJob")


@dataclass(frozen=True)
class MediaRetryState:
    signature: tuple[int, int]
    failures: int
    retry_after: float


@dataclass(frozen=True)
class InputValidationResult:
    path: Path
    ready: bool
    quarantine: bool = False
    attempts: int = 0
    reason: str = ""


_MEDIA_RETRY_BY_PATH: dict[str, MediaRetryState] = {}
_INCOMPLETE_MEDIA_ERRORS = (
    "moov atom not found",
    "invalid data found when processing input",
    "end of file",
    "partial file",
    "error reading header",
    "could not find codec parameters",
)


@dataclass(frozen=True)
class RecoveryScanResult:
    scanned_videos: int
    reopened_history_records: int
    pending_jobs: int

    @property
    def message(self) -> str:
        if self.reopened_history_records:
            return (
                f"Varredura de recuperação: {self.scanned_videos} vídeo(s) verificados, "
                f"{self.reopened_history_records} pendência(s) reaberta(s)."
            )
        if self.pending_jobs:
            return (
                f"Varredura de recuperação: {self.pending_jobs} trabalho(s) pendente(s) "
                "reencontrado(s) na entrada."
            )
        return f"Varredura de recuperação concluída: {self.scanned_videos} vídeo(s) verificados, sem pendências."


@dataclass
class IdleRescanTracker:
    threshold: int
    idle_checks: int = 0

    @classmethod
    def from_config(cls, config: AppConfig) -> "IdleRescanTracker":
        threshold = max(1, int(getattr(config, "idle_rescan_checks", 6) or 6))
        return cls(threshold=threshold)

    def record(self, work_found: bool) -> bool:
        if work_found:
            self.idle_checks = 0
            return False
        self.idle_checks += 1
        if self.idle_checks < self.threshold:
            return False
        self.idle_checks = 0
        return True


def _history(config: AppConfig) -> RenderHistory | None:
    return RenderHistory(config.history_path, config.input_path) if config.history_enabled else None


def rescan_pending_input(config: AppConfig) -> RecoveryScanResult:
    """Audita a entrada e reabre somente históricos que perderam o arquivo de saída."""
    history = _history(config)
    videos = all_input_videos(config)
    reopened = history.reopen_missing_output_records(videos) if history else 0
    render_format = effective_render_format(config)
    if render_format == "single":
        pending_jobs = len(candidate_manual_82_videos(config, history))
    elif render_format == "all":
        pending_jobs = len(candidate_all_videos(config, history))
    else:
        pending_jobs = len(candidate_video_groups(config, history))
    return RecoveryScanResult(
        scanned_videos=len(videos),
        reopened_history_records=reopened,
        pending_jobs=pending_jobs,
    )


def _worker_count(config: AppConfig, *, one_only: bool = False) -> int:
    if one_only:
        return 1
    # Trava de segurança: o hardware alvo foi limitado a 2 renders simultâneos.
    return clamp_parallel_workers(getattr(config, "parallel_workers", 1) or 1)


def _media_key(path: Path) -> str:
    return str(path.absolute()).casefold()


def _media_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _looks_incomplete_media(error: ProbeError) -> bool:
    message = str(error).casefold()
    return any(pattern in message for pattern in _INCOMPLETE_MEDIA_ERRORS)


def _record_incomplete_media(
    path: Path,
    signature: tuple[int, int],
    config: AppConfig,
    on_status: Callable[[str], None],
    reason: str,
) -> InputValidationResult:
    key = _media_key(path)
    with _MEDIA_RETRY_LOCK:
        previous = _MEDIA_RETRY_BY_PATH.get(key)
        failures = previous.failures + 1 if previous and previous.signature == signature else 1
        delay_seconds = min(300, 15 * (2 ** min(failures - 1, 5)))
        _MEDIA_RETRY_BY_PATH[key] = MediaRetryState(
            signature=signature,
            failures=failures,
            retry_after=time.monotonic() + delay_seconds,
        )

    attempt_limit = max(2, int(getattr(config, "quarantine_attempts", 6) or 6))
    if failures >= attempt_limit:
        on_status(
            f"Quarentena programada: {path.name} permaneceu inválido após {failures} tentativas."
        )
        return InputValidationResult(
            path=path,
            ready=False,
            quarantine=True,
            attempts=failures,
            reason=reason,
        )

    on_status(
        f"Aguardando arquivo válido: {path.name}; MP4 incompleto ou não finalizado "
        f"(tentativa {failures}/{attempt_limit}, nova verificação automática)."
    )
    return InputValidationResult(
        path=path,
        ready=False,
        attempts=failures,
        reason=reason,
    )


def _input_video_is_ready(
    path: Path,
    config: AppConfig,
    on_status: Callable[[str], None],
) -> InputValidationResult:
    """Confirma estabilidade e estrutura do MP4 sem transformar cópia incompleta em erro."""
    key = _media_key(path)
    signature = _media_signature(path)
    if signature is None:
        on_status(f"Aguardando arquivo válido: {path.name}; arquivo ausente.")
        return InputValidationResult(path=path, ready=False)

    now = time.monotonic()
    with _MEDIA_RETRY_LOCK:
        retry = _MEDIA_RETRY_BY_PATH.get(key)
        if retry and retry.signature != signature:
            _MEDIA_RETRY_BY_PATH.pop(key, None)
            retry = None
        if retry and now < retry.retry_after:
            on_status(
                f"Aguardando arquivo válido: {path.name}; MP4 ainda incompleto ou não finalizado."
            )
            return InputValidationResult(
                path=path,
                ready=False,
                attempts=retry.failures,
                reason="MP4 incompleto ou não finalizado",
            )

    if signature[0] <= 0:
        return _record_incomplete_media(
            path,
            signature,
            config,
            on_status,
            "arquivo vazio",
        )

    if not wait_for_stable_file(
        path,
        config.stable_check_seconds,
        config.stable_check_interval,
        config.stable_check_max_wait_seconds,
    ):
        on_status(f"Aguardando arquivo válido: {path.name}; a cópia ainda está em andamento.")
        return InputValidationResult(path=path, ready=False)

    try:
        probe_media(path, config.ffprobe_timeout_seconds)
    except ProbeError as exc:
        if not _looks_incomplete_media(exc):
            # Deixa o renderer apresentar erros permanentes de configuração,
            # como FFprobe ausente, com a mensagem técnica completa.
            return InputValidationResult(path=path, ready=True)

        signature = _media_signature(path) or signature
        return _record_incomplete_media(
            path,
            signature,
            config,
            on_status,
            str(exc),
        )

    with _MEDIA_RETRY_LOCK:
        _MEDIA_RETRY_BY_PATH.pop(key, None)
    return InputValidationResult(path=path, ready=True)


def _job_input_paths(job: RenderJob) -> tuple[Path, ...]:
    paths = getattr(job, "paths", None)
    if paths:
        return tuple(Path(path) for path in paths)
    return (Path(job),)


def _folder_inputs_are_ready(
    jobs: Sequence[RenderJob],
    source_folder: Path,
    config: AppConfig,
    on_status: Callable[[str], None],
) -> bool:
    paths: list[Path] = []
    seen: set[str] = set()
    for job in jobs:
        for path in _job_input_paths(job):
            key = _media_key(path)
            if key not in seen:
                seen.add(key)
                paths.append(path)

    if not paths:
        return False
    validation_workers = min(4, len(paths))
    with ThreadPoolExecutor(max_workers=validation_workers, thread_name_prefix="autorender-check") as executor:
        futures = [executor.submit(_input_video_is_ready, path, config, on_status) for path in paths]
        validations = [future.result() for future in as_completed(futures)]

    quarantine_by_key = {
        _media_key(validation.path): validation
        for validation in validations
        if validation.quarantine
    }
    quarantined_jobs: set[tuple[str, ...]] = set()
    for job in jobs:
        job_paths = _job_input_paths(job)
        job_keys = tuple(_media_key(path) for path in job_paths)
        matched = [quarantine_by_key[key] for key in job_keys if key in quarantine_by_key]
        if not matched or job_keys in quarantined_jobs:
            continue
        quarantined_jobs.add(job_keys)
        failure = max(matched, key=lambda item: item.attempts)
        try:
            result = quarantine_files(
                job_paths,
                source_folder,
                config,
                failed_file=failure.path,
                reason=failure.reason,
                attempts=failure.attempts,
            )
        except OSError as exc:
            on_status(f"Falha ao mover {failure.path.name} para a quarentena: {exc}")
            continue

        with _MEDIA_RETRY_LOCK:
            for path in job_paths:
                _MEDIA_RETRY_BY_PATH.pop(_media_key(path), None)
        on_status(
            f"Quarentena: {len(result.moved_files)} arquivo(s) movido(s) para "
            f"{result.destination_folder}."
        )

    return all(validation.ready for validation in validations)


def _render_single_candidate(path: Path, config: AppConfig, on_status: Callable[[str], None]) -> int:
    on_status(f"Renderizando: {path}.")

    try:
        result = render_one(path, config)
        delivery = "; aguardando fim da pasta para enviar à saída" if getattr(result, "delivery_pending", False) else ""
        on_status(f"OK: {path.name} em {result.elapsed_seconds:.2f}s{delivery}")
    except RenderError as exc:
        on_status(f"ERRO: {path.name}: {exc}")
        return 0
    return 1


def _render_candidate_group(group: CandidateVideoGroup, config: AppConfig, on_status: Callable[[str], None]) -> int:
    if not group.is_pair:
        return _render_single_candidate(group.primary_path, config, on_status)

    input_81, input_82 = group.paths
    on_status(f"Renderizando par: {input_81.name} + {input_82.name}.")

    try:
        result = render_pair(input_81, input_82, config)
        delivery = "; aguardando fim da pasta para enviar à saída" if getattr(result, "delivery_pending", False) else ""
        on_status(f"OK par: {input_81.name} + {input_82.name} em {result.elapsed_seconds:.2f}s{delivery}")
    except RenderError as exc:
        on_status(f"ERRO par {input_81.name} + {input_82.name}: {exc}")
        return 0
    return 1


def _render_both_candidate_group(group: CandidateVideoGroup, config: AppConfig, on_status: Callable[[str], None]) -> int:
    if not group.is_pair:
        on_status(f"Sem par 81/82 para {group.primary_path.name}; gerando apenas o formato de 1 vídeo.")
        return _render_single_candidate(group.primary_path, config, on_status)

    input_81, input_82 = group.paths
    on_status(f"Renderizando ambos: {input_81.name} + {input_82.name}.")

    try:
        result = render_both(input_81, input_82, config)
        on_status(
            f"OK ambos: {input_81.name} + {input_82.name}; "
            f"2 formatos em {result.elapsed_seconds:.2f}s"
        )
    except RenderError as exc:
        on_status(f"ERRO ambos {input_81.name} + {input_82.name}: {exc}")
        return 0
    return 1


def _run_render_jobs(
    jobs: Sequence[RenderJob],
    config: AppConfig,
    on_status: Callable[[str], None],
    render_job: Callable[[RenderJob, AppConfig, Callable[[str], None]], int],
    *,
    one_only: bool = False,
    should_stop: Callable[[], bool] | None = None,
    parallel_message: str | None = None,
) -> int:
    if one_only:
        jobs = jobs[:1]
    if not jobs:
        return 0

    jobs_by_folder: dict[Path, list[RenderJob]] = {}
    for job in jobs:
        primary_path = Path(getattr(job, "primary_path", job))
        source_folder = cycle_folder_for_path(primary_path, config) or primary_path.parent
        jobs_by_folder.setdefault(source_folder, []).append(job)

    processed = 0
    max_workers = _worker_count(config, one_only=one_only)
    for source_folder, folder_jobs in jobs_by_folder.items():
        if should_stop and should_stop():
            on_status("Parada solicitada: nenhum novo video sera iniciado.")
            break

        if not _folder_inputs_are_ready(folder_jobs, source_folder, config, on_status):
            remaining_paths = [
                path
                for job in folder_jobs
                for path in _job_input_paths(job)
                if path.exists()
            ]
            if remaining_paths:
                on_status(
                    f"Pasta {source_folder.name} pendente: aguardando todos os vídeos ficarem válidos."
                )
            else:
                on_status(f"Pasta {source_folder.name} retirada da fila após a quarentena.")
            continue

        workers = min(max_workers, len(folder_jobs))
        folder_processed = 0
        on_status(
            f"Pasta {source_folder.name}: {len(folder_jobs)} render(s); "
            f"envio liberado ao concluir esta pasta."
        )
        with defer_deliveries_until_batch_complete():
            if workers <= 1:
                for job in folder_jobs:
                    if should_stop and should_stop():
                        on_status("Parada solicitada: nenhum novo video sera iniciado.")
                        break
                    result = render_job(job, config, on_status)
                    processed += result
                    folder_processed += result
            else:
                if parallel_message:
                    on_status(parallel_message.format(count=len(folder_jobs), workers=workers))

                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="autorender") as executor:
                    for idx in range(0, len(folder_jobs), workers):
                        if should_stop and should_stop():
                            on_status("Parada solicitada: nenhum novo video sera iniciado.")
                            break
                        batch = folder_jobs[idx: idx + workers]
                        futures = [executor.submit(render_job, job, config, on_status) for job in batch]
                        for future in as_completed(futures):
                            result = int(future.result())
                            processed += result
                            folder_processed += result
        if folder_processed == len(folder_jobs):
            on_status(f"Pasta {source_folder.name} finalizada; envio em segundo plano liberado.")
        else:
            on_status(
                f"Pasta {source_folder.name} parcialmente processada; "
                "os itens com falha continuarão pendentes."
            )
    return processed


def _render_available_candidates(
    config: AppConfig,
    on_status: Callable[[str], None],
    *,
    one_only: bool = False,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    # Evita que múltiplos eventos do watchdog iniciem duas filas ao mesmo tempo.
    if not _PROCESS_LOCK.acquire(blocking=False):
        return 0

    try:
        history = _history(config)
        groups = candidate_video_groups(config, history)
        return _run_render_jobs(
            groups,
            config,
            on_status,
            _render_candidate_group,
            one_only=one_only,
            should_stop=should_stop,
            parallel_message="Fila paralela: {count} render(s), {workers} simultâneo(s).",
        )
    finally:
        _PROCESS_LOCK.release()


def render_available_candidates(
    config: AppConfig,
    on_status: Callable[[str], None] | None = None,
    *,
    one_only: bool = False,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Renderiza a fila elegivel usando parallel_workers do settings.json."""
    return _render_available_candidates(
        config,
        on_status or (lambda msg: print(msg)),
        one_only=one_only,
        should_stop=should_stop,
    )


def render_both_candidates(
    config: AppConfig,
    on_status: Callable[[str], None] | None = None,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Gera os formatos de uma e duas câmeras para cada par elegível."""
    on_status = on_status or (lambda msg: print(msg))
    if not _PROCESS_LOCK.acquire(blocking=False):
        return 0

    try:
        history = _history(config)
        groups = candidate_video_groups(config, history)
        return _run_render_jobs(
            groups,
            config,
            on_status,
            _render_both_candidate_group,
            should_stop=should_stop,
            parallel_message="Fila ambos: {count} grupo(s), {workers} render(s) simultâneo(s).",
        )
    finally:
        _PROCESS_LOCK.release()


def render_manual_82_candidates(
    config: AppConfig,
    on_status: Callable[[str], None] | None = None,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Render manual: usa somente vídeos 82... e o preset simples."""
    on_status = on_status or (lambda msg: print(msg))
    if not _PROCESS_LOCK.acquire(blocking=False):
        return 0

    try:
        history = _history(config)
        paths = candidate_manual_82_videos(config, history)
        return _run_render_jobs(
            paths,
            config,
            on_status,
            _render_single_candidate,
            should_stop=should_stop,
            parallel_message="Fila manual 82: {count} vídeo(s), {workers} render(s) simultâneo(s).",
        )
    finally:
        _PROCESS_LOCK.release()


def render_all_candidates(
    config: AppConfig,
    on_status: Callable[[str], None] | None = None,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Renderiza individualmente todos os vídeos elegíveis da pasta/lote ativo."""
    on_status = on_status or (lambda msg: print(msg))
    if not _PROCESS_LOCK.acquire(blocking=False):
        return 0

    try:
        history = _history(config)
        paths = candidate_all_videos(config, history)
        return _run_render_jobs(
            paths,
            config,
            on_status,
            _render_single_candidate,
            should_stop=should_stop,
            parallel_message="Fila completa: {count} vídeo(s), {workers} render(s) simultâneo(s).",
        )
    finally:
        _PROCESS_LOCK.release()


def render_configured_candidates(
    config: AppConfig,
    on_status: Callable[[str], None] | None = None,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Executa a fila conforme o formato efetivo, incluindo a detecção de 1 preset."""
    render_format = effective_render_format(config)
    if render_format == "single":
        return render_manual_82_candidates(config, on_status, should_stop=should_stop)
    if render_format == "both":
        return render_both_candidates(config, on_status, should_stop=should_stop)
    if render_format == "all":
        return render_all_candidates(config, on_status, should_stop=should_stop)
    return render_available_candidates(config, on_status, should_stop=should_stop)


def run_polling_watcher(config: AppConfig, on_status: Callable[[str], None] | None = None) -> None:
    """Fallback watcher sem dependência externa. Use Ctrl+C para parar."""
    input_dir = config.input_path
    input_dir.mkdir(parents=True, exist_ok=True)
    on_status = on_status or (lambda msg: print(msg))
    workers = _worker_count(config)
    render_format = effective_render_format(config)
    mode = f"*_AUTO/*_MANUAL_TODAS/Manual/Manual_Retry primeiro; formato={render_format}; paralelo={workers}"
    on_status(f"Monitorando ({mode}): {input_dir}")
    recovery_tracker = IdleRescanTracker.from_config(config)
    while True:
        history = _history(config)
        if effective_render_format(config) == "all":
            pending = candidate_all_videos(config, history)
        elif effective_render_format(config) == "single":
            pending = candidate_manual_82_videos(config, history)
        else:
            pending = candidate_video_groups(config, history)
        if pending:
            render_configured_candidates(config, on_status)
            recovery_tracker.record(True)
        elif recovery_tracker.record(False):
            result = rescan_pending_input(config)
            on_status(result.message)
            if result.pending_jobs:
                render_configured_candidates(config, on_status)
        time.sleep(1.0)


def run_watchdog_watcher(config: AppConfig, on_status: Callable[[str], None] | None = None) -> None:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError as exc:
        raise WatcherError("watchdog não instalado. Rode scripts/install_dependencies.bat ou use polling.") from exc

    on_status = on_status or (lambda msg: print(msg))
    stop_event = threading.Event()
    recovery_tracker = IdleRescanTracker.from_config(config)

    class RenderScheduler:
        def __init__(self) -> None:
            self._pending = threading.Event()
            self._lock = threading.Lock()
            self._thread: threading.Thread | None = None

        def request(self) -> None:
            recovery_tracker.record(True)
            self._pending.set()
            with self._lock:
                if self._thread and self._thread.is_alive():
                    return
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()

        def _loop(self) -> None:
            while self._pending.is_set() and not stop_event.is_set():
                self._pending.clear()
                processed = render_configured_candidates(
                    config,
                    on_status,
                    should_stop=stop_event.is_set,
                )
                if processed > 0:
                    # Repassa a pasta para pegar arquivos que chegaram durante o render.
                    self._pending.set()

        def join(self, timeout: float = 5.0) -> None:
            thread = self._thread
            if thread and thread.is_alive():
                thread.join(timeout)

    scheduler = RenderScheduler()

    class Handler(FileSystemEventHandler):
        def on_created(self, event):
            # Mesmo quando o evento é de um vídeo solto, o seletor respeita a regra:
            # se existir *_AUTO ativa, vídeos soltos ficam ignorados até não haver ciclos.
            path = Path(event.src_path)
            if event.is_directory:
                return
            if not is_video_file(path):
                return
            scheduler.request()

        def on_moved(self, event):
            path = Path(event.dest_path)
            if not is_video_file(path):
                return
            scheduler.request()

    input_dir = config.input_path
    input_dir.mkdir(parents=True, exist_ok=True)
    observer = Observer()
    observer.schedule(Handler(), str(input_dir), recursive=config.recursive_input)
    observer.start()
    workers = _worker_count(config)
    render_format = effective_render_format(config)
    mode = f"*_AUTO/*_MANUAL_TODAS/Manual/Manual_Retry primeiro; formato={render_format}; paralelo={workers}"
    on_status(f"Monitorando com watchdog ({mode}): {input_dir}")
    try:
        # Processa pendências que já estavam na pasta antes de iniciar o watchdog.
        scheduler.request()
        while True:
            time.sleep(5.0)
            if recovery_tracker.record(False):
                result = rescan_pending_input(config)
                on_status(result.message)
                if result.pending_jobs:
                    scheduler.request()
    except KeyboardInterrupt:
        stop_event.set()
        observer.stop()
    observer.join()
    scheduler.join()
