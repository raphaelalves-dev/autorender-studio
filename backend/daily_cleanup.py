from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from backend.config import AppConfig


@dataclass(frozen=True)
class ProcessedCleanupResult:
    performed: bool
    completed: bool
    removed_files: int = 0
    removed_folders: int = 0
    freed_bytes: int = 0
    error_count: int = 0
    subject: str = "processados"

    @property
    def message(self) -> str:
        if not self.performed:
            return ""
        if not self.completed:
            return (
                f"Limpeza diária de {self.subject} parcial: {self.removed_files} arquivo(s) e "
                f"{self.removed_folders} pasta(s) removidos. A tentativa será repetida na próxima abertura."
            )
        if not self.removed_files and not self.removed_folders:
            return f"Limpeza diária de {self.subject} concluída: nenhum item anterior encontrado."
        return (
            f"Limpeza diária de {self.subject} concluída: {self.removed_files} arquivo(s), "
            f"{self.removed_folders} pasta(s) e {_format_bytes(self.freed_bytes)} liberados."
        )


def _format_bytes(value: int) -> str:
    if value < 1024 * 1024:
        return f"{max(0, value // 1024)} KB"
    return f"{value / (1024 * 1024):.1f} MB"


def _is_older_than_today(timestamp: float, today: date) -> bool:
    return datetime.fromtimestamp(timestamp).date() < today


def _item_stats(path: Path) -> tuple[float, int, int]:
    """Retorna a data mais recente, o total de arquivos e o tamanho do item."""
    stat = path.stat()
    latest_mtime = stat.st_mtime
    if not path.is_dir():
        return latest_mtime, 1, stat.st_size

    file_count = 0
    total_bytes = 0
    for child in path.rglob("*"):
        if child.is_symlink():
            continue
        child_stat = child.stat()
        latest_mtime = max(latest_mtime, child_stat.st_mtime)
        if child.is_file():
            file_count += 1
            total_bytes += child_stat.st_size
    return latest_mtime, file_count, total_bytes


def _is_safe_processed_item(item: Path, processed_root: Path) -> bool:
    if item.is_symlink():
        return False
    try:
        item.resolve().relative_to(processed_root)
    except ValueError:
        return False
    return True


def _cleanup_root_before_today(root: Path, current_day: date) -> tuple[int, int, int, int]:
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    removed_files = 0
    removed_folders = 0
    freed_bytes = 0
    error_count = 0

    for item in resolved_root.iterdir():
        if item.name == ".gitkeep" or not _is_safe_processed_item(item, resolved_root):
            continue
        try:
            latest_mtime, file_count, item_bytes = _item_stats(item)
            if not _is_older_than_today(latest_mtime, current_day):
                continue

            if item.is_dir():
                shutil.rmtree(item)
                removed_folders += 1
                removed_files += file_count
            else:
                item.unlink()
                removed_files += 1
            freed_bytes += item_bytes
        except OSError:
            error_count += 1
    return removed_files, removed_folders, freed_bytes, error_count


def cleanup_processed_before_today(config: AppConfig, *, today: date | None = None) -> ProcessedCleanupResult:
    """Remove itens de processados anteriores ao dia atual, uma vez por data."""
    current_day = today or date.today()
    current_day_key = current_day.isoformat()
    if getattr(config, "processed_cleanup_last_run", "") == current_day_key:
        return ProcessedCleanupResult(performed=False, completed=True)

    removed_files, removed_folders, freed_bytes, error_count = _cleanup_root_before_today(
        config.processed_path, current_day
    )

    completed = error_count == 0
    if completed:
        config.processed_cleanup_last_run = current_day_key
    return ProcessedCleanupResult(
        performed=True,
        completed=completed,
        removed_files=removed_files,
        removed_folders=removed_folders,
        freed_bytes=freed_bytes,
        error_count=error_count,
    )


def cleanup_quarantine_before_today(config: AppConfig, *, today: date | None = None) -> ProcessedCleanupResult:
    """Remove a quarentena de dias anteriores uma vez por data."""
    current_day = today or date.today()
    current_day_key = current_day.isoformat()
    if getattr(config, "quarantine_cleanup_last_run", "") == current_day_key:
        return ProcessedCleanupResult(performed=False, completed=True, subject="quarentena")

    removed_files, removed_folders, freed_bytes, error_count = _cleanup_root_before_today(
        config.quarantine_path, current_day
    )
    completed = error_count == 0
    if completed:
        config.quarantine_cleanup_last_run = current_day_key
    return ProcessedCleanupResult(
        performed=True,
        completed=completed,
        removed_files=removed_files,
        removed_folders=removed_folders,
        freed_bytes=freed_bytes,
        error_count=error_count,
        subject="quarentena",
    )
