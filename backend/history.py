from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


_HISTORY_LOCK = threading.RLock()
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv"}


def _video_files(folder: Path) -> list[Path]:
    try:
        return sorted(
            [path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in _VIDEO_EXTENSIONS],
            key=lambda path: str(path).lower(),
        )
    except OSError:
        return []


@dataclass
class VideoHistoryRecord:
    key: str
    relative_path: str
    source_path: str
    output_path: str
    size_bytes: int
    mtime_ns: int
    status: str
    source_mode: str
    cycle_folder: Optional[str]
    preset_name: str
    elapsed_seconds: Optional[float]
    completed_at: str


@dataclass
class CycleHistoryRecord:
    key: str
    relative_path: str
    source_path: str
    status: str
    video_count: int
    completed_at: str


class RenderHistory:
    """Histórico simples em JSON para evitar reprocessar ciclos e vídeos soltos."""

    def __init__(self, path: str | Path, input_root: str | Path):
        self.path = Path(path)
        self.input_root = Path(input_root)
        self.data: Dict[str, Any] = {
            "version": 1,
            "updated_at": None,
            "videos": {},
            "cycles": {},
        }
        self.load()

    def load(self) -> None:
        with _HISTORY_LOCK:
            if not self.path.exists():
                return
            try:
                with self.path.open("r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    self.data.update(loaded)
                    self.data.setdefault("videos", {})
                    self.data.setdefault("cycles", {})
            except Exception:
                # Histórico corrompido não deve parar render. O arquivo antigo é preservado
                # com sufixo .corrupt para análise posterior.
                corrupt = self.path.with_suffix(
                    f"{self.path.suffix}.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                )
                try:
                    self.path.replace(corrupt)
                except OSError:
                    pass
                self.data = {"version": 1, "updated_at": None, "videos": {}, "cycles": {}}

    def save(self) -> None:
        with _HISTORY_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.data["updated_at"] = datetime.now().isoformat(timespec="seconds")
            tmp_path = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            tmp_path.replace(self.path)

    def _relative(self, path: str | Path) -> str:
        p = Path(path)
        try:
            rel = p.resolve().relative_to(self.input_root.resolve())
            return rel.as_posix()
        except ValueError:
            return p.resolve().as_posix()

    def video_key(self, path: str | Path) -> str:
        p = Path(path)
        stat = p.stat()
        return self.video_key_from_stat(p, stat.st_size, stat.st_mtime_ns)

    def video_key_from_stat(self, path: str | Path, size_bytes: int, mtime_ns: int) -> str:
        p = Path(path)
        rel = self._relative(p).lower()
        return f"{rel}|{size_bytes}|{mtime_ns}"

    def cycle_key(self, folder: str | Path) -> str:
        return self._relative(folder).lower()

    def is_video_done(self, path: str | Path) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        with _HISTORY_LOCK:
            try:
                key = self.video_key(p)
            except OSError:
                return False
            record = self.data.get("videos", {}).get(key)
            return bool(record and record.get("status") == "success")

    def is_cycle_done(self, folder: str | Path) -> bool:
        current_videos = _video_files(Path(folder))
        if current_videos:
            # O conteúdo atual é a fonte da verdade. Uma pasta reutilizada com o
            # mesmo nome só continua concluída se todos os arquivos forem os mesmos.
            return all(self.is_video_done(video) for video in current_videos)

        with _HISTORY_LOCK:
            key = self.cycle_key(folder)
            record = self.data.get("cycles", {}).get(key)
            return bool(record and record.get("status") == "complete")

    def mark_video_success(
        self,
        source_path: str | Path,
        output_path: str | Path,
        *,
        source_mode: str,
        cycle_folder: Optional[str | Path],
        preset_name: str,
        elapsed_seconds: Optional[float],
        size_bytes: Optional[int] = None,
        mtime_ns: Optional[int] = None,
    ) -> None:
        source = Path(source_path)
        if size_bytes is None or mtime_ns is None:
            if not source.exists():
                return
            try:
                stat = source.stat()
            except OSError:
                return
            size_bytes = stat.st_size
            mtime_ns = stat.st_mtime_ns
        with _HISTORY_LOCK:
            self.load()
            cycle_rel = self._relative(cycle_folder) if cycle_folder else None
            record = VideoHistoryRecord(
                key=self.video_key_from_stat(source, size_bytes, mtime_ns),
                relative_path=self._relative(source),
                source_path=str(source),
                output_path=str(output_path),
                size_bytes=size_bytes,
                mtime_ns=mtime_ns,
                status="success",
                source_mode=source_mode,
                cycle_folder=cycle_rel,
                preset_name=preset_name,
                elapsed_seconds=elapsed_seconds,
                completed_at=datetime.now().isoformat(timespec="seconds"),
            )
            self.data.setdefault("videos", {})[record.key] = asdict(record)
            self.save()

    def reopen_missing_output_records(self, source_paths: Iterable[str | Path]) -> int:
        """Reabre apenas entradas cujo arquivo de saída desapareceu ou ficou vazio."""
        with _HISTORY_LOCK:
            self.load()
            video_records = self.data.setdefault("videos", {})
            reopened = 0
            for source_path in source_paths:
                source = Path(source_path)
                if not source.exists():
                    continue
                try:
                    key = self.video_key(source)
                except OSError:
                    continue
                record = video_records.get(key)
                if not record or record.get("status") != "success":
                    continue

                output_value = str(record.get("output_path") or "").strip()
                output = Path(output_value) if output_value else None
                try:
                    output_is_valid = bool(output and output.exists() and output.stat().st_size > 0)
                except OSError:
                    output_is_valid = False
                if output_is_valid:
                    continue

                video_records.pop(key, None)
                reopened += 1

            if reopened:
                self.save()
            return reopened

    def mark_cycle_complete(self, folder: str | Path, *, video_count: int) -> None:
        p = Path(folder)
        with _HISTORY_LOCK:
            self.load()
            record = CycleHistoryRecord(
                key=self.cycle_key(p),
                relative_path=self._relative(p),
                source_path=str(p),
                status="complete",
                video_count=video_count,
                completed_at=datetime.now().isoformat(timespec="seconds"),
            )
            self.data.setdefault("cycles", {})[record.key] = asdict(record)
            self.save()

    def clear(self) -> None:
        with _HISTORY_LOCK:
            self.data = {"version": 1, "updated_at": None, "videos": {}, "cycles": {}}
            self.save()
