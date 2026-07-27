from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import threading
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


_LOG_LOCK = threading.RLock()
_MB = 1024 * 1024


def _rotate_file(path: Path, *, max_bytes: int, backup_count: int) -> None:
    """Rotaciona um arquivo antes da próxima escrita, com retenção limitada."""
    if not path.exists() or path.stat().st_size < max_bytes:
        return
    for index in range(backup_count, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if index == backup_count:
            source.unlink(missing_ok=True)
            continue
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def setup_logger(logs_dir: Path, name: str = "autorender") -> logging.Logger:
    with _LOG_LOCK:
        logs_dir.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        configured_dir = getattr(logger, "_autorender_logs_dir", None)
        if configured_dir == str(logs_dir.resolve()) and logger.handlers:
            return logger

        logger.handlers.clear()
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

        try:
            file_handler = RotatingFileHandler(
                logs_dir / "autorender.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            pass

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        setattr(logger, "_autorender_logs_dir", str(logs_dir.resolve()))
        return logger


def append_jsonl(logs_dir: Path, filename: str, data: Dict[str, Any]) -> None:
    with _LOG_LOCK:
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            path = logs_dir / filename
            _rotate_file(path, max_bytes=10 * _MB, backup_count=2)
            row = dict(data)
            row.setdefault("logged_at", datetime.now().isoformat(timespec="seconds"))
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            return


def append_text_line(
    path: Path,
    line: str,
    *,
    max_bytes: int = 2 * _MB,
    backup_count: int = 2,
) -> None:
    """Acrescenta uma linha de texto sem permitir crescimento ilimitado."""
    with _LOG_LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_file(path, max_bytes=max_bytes, backup_count=backup_count)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line.rstrip("\n") + "\n")
        except OSError:
            return
