from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from backend.logger import setup_logger


@dataclass(frozen=True)
class DeliveryTicket:
    local_path: Path
    final_path: Path
    manifest_path: Path
    ready_event: threading.Event | None = None


_QUEUE: queue.Queue[DeliveryTicket] = queue.Queue()
_LOCK = threading.RLock()
_QUEUED_MANIFESTS: set[str] = set()
_WORKER: threading.Thread | None = None
_ACTIVE_DELIVERY_GATE: threading.Event | None = None
_DEFER_COUNT = 0


def staging_root(config) -> Path:
    configured = str(getattr(config, "local_staging_dir", "render_staging") or "render_staging")
    path = Path(configured)
    return path if path.is_absolute() else config.root_path() / path


def should_stage_output(final_path: Path, config) -> bool:
    """Usa o SSD do app quando a saída está em outra unidade ou caminho UNC."""
    if not bool(getattr(config, "local_staging_enabled", True)):
        return False
    final_text = str(final_path)
    if final_text.startswith("\\\\"):
        return True
    app_drive = config.root_path().drive.lower()
    final_drive = final_path.drive.lower()
    return bool(final_drive and app_drive and final_drive != app_drive)


def local_render_path(final_path: Path, config) -> Path:
    if not should_stage_output(final_path, config):
        return final_path
    root = staging_root(config)
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{uuid.uuid4().hex}_{final_path.name}"


def _manifest_path(local_path: Path) -> Path:
    return local_path.with_name(f"{local_path.name}.delivery.json")


def _write_manifest(local_path: Path, final_path: Path) -> Path:
    manifest = _manifest_path(local_path)
    temporary = manifest.with_name(f"{manifest.name}.{uuid.uuid4().hex}.tmp")
    payload = {"local_path": str(local_path), "final_path": str(final_path)}
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(manifest)
    return manifest


def _read_manifest(path: Path) -> DeliveryTicket | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        local = Path(str(data["local_path"]))
        final = Path(str(data["final_path"]))
        if not local.exists():
            path.unlink(missing_ok=True)
            return None
        return DeliveryTicket(local, final, path)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _deliver_once(ticket: DeliveryTicket) -> None:
    ticket.final_path.parent.mkdir(parents=True, exist_ok=True)
    partial = ticket.final_path.with_name(
        f".{ticket.final_path.name}.{uuid.uuid4().hex}.autorender-part"
    )
    try:
        shutil.copy2(ticket.local_path, partial)
        os.replace(partial, ticket.final_path)
        ticket.local_path.unlink(missing_ok=True)
        ticket.manifest_path.unlink(missing_ok=True)
    finally:
        partial.unlink(missing_ok=True)


def _worker_loop(logs_path: Path) -> None:
    logger = setup_logger(logs_path)
    while True:
        ticket = _QUEUE.get()
        key = str(ticket.manifest_path.resolve()).lower()
        try:
            if ticket.ready_event is not None:
                ticket.ready_event.wait()
            logger.info("Enviando para a saída: %s", ticket.final_path)
            _deliver_once(ticket)
            logger.info("Entrega concluída: %s", ticket.final_path)
        except OSError as exc:
            logger.warning("Saída indisponível; nova tentativa em 15s: %s", exc)
            time.sleep(15)
            _QUEUE.put(ticket)
            continue
        finally:
            if not ticket.manifest_path.exists():
                with _LOCK:
                    _QUEUED_MANIFESTS.discard(key)
            _QUEUE.task_done()


def _ensure_worker(logs_path: Path) -> None:
    global _WORKER
    with _LOCK:
        if _WORKER and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(
            target=_worker_loop,
            args=(logs_path,),
            name="autorender-delivery",
            daemon=True,
        )
        _WORKER.start()


def _enqueue(ticket: DeliveryTicket, logs_path: Path) -> None:
    key = str(ticket.manifest_path.resolve()).lower()
    with _LOCK:
        if key in _QUEUED_MANIFESTS:
            return
        _QUEUED_MANIFESTS.add(key)
        _QUEUE.put(ticket)
    _ensure_worker(logs_path)


def resume_pending_deliveries(config) -> int:
    root = staging_root(config)
    if not root.exists():
        return 0
    count = 0
    for manifest in root.glob("*.delivery.json"):
        ticket = _read_manifest(manifest)
        if ticket:
            _enqueue(ticket, config.logs_path)
            count += 1
    return count


def queue_delivery(local_path: Path, final_path: Path, config) -> DeliveryTicket:
    manifest = _write_manifest(local_path, final_path)
    with _LOCK:
        ready_event = _ACTIVE_DELIVERY_GATE
    ticket = DeliveryTicket(local_path, final_path, manifest, ready_event)
    _enqueue(ticket, config.logs_path)
    return ticket


def pending_delivery_count() -> int:
    with _LOCK:
        return len(_QUEUED_MANIFESTS)


@contextmanager
def defer_deliveries_until_batch_complete():
    """Anexa as entregas do lote a uma trava liberada quando ele terminar."""
    global _ACTIVE_DELIVERY_GATE, _DEFER_COUNT
    with _LOCK:
        if _DEFER_COUNT == 0:
            _ACTIVE_DELIVERY_GATE = threading.Event()
        _DEFER_COUNT += 1
    try:
        yield
    finally:
        with _LOCK:
            _DEFER_COUNT = max(0, _DEFER_COUNT - 1)
            if _DEFER_COUNT == 0:
                gate = _ACTIVE_DELIVERY_GATE
                _ACTIVE_DELIVERY_GATE = None
                if gate is not None:
                    gate.set()


def deliveries_are_deferred() -> bool:
    with _LOCK:
        return _ACTIVE_DELIVERY_GATE is not None and not _ACTIVE_DELIVERY_GATE.is_set()
