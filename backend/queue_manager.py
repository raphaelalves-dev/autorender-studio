from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv"}


@dataclass
class QueueItem:
    path: Path
    status: str = "pendente"
    elapsed_seconds: Optional[float] = None
    error: Optional[str] = None


@dataclass
class RenderQueue:
    items: List[QueueItem] = field(default_factory=list)

    def add(self, path: str | Path) -> None:
        p = Path(path)
        if p.suffix.lower() in VIDEO_EXTENSIONS and all(item.path != p for item in self.items):
            self.items.append(QueueItem(path=p))

    def add_many(self, paths: Iterable[str | Path]) -> None:
        for path in paths:
            self.add(path)

    def pending(self) -> List[QueueItem]:
        return [item for item in self.items if item.status == "pendente"]


def is_video_file(path: Path) -> bool:
    try:
        return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    except OSError:
        return False


def wait_for_stable_file(
    path: str | Path,
    stable_seconds: float = 3.0,
    interval: float = 1.0,
    max_wait_seconds: float = 120.0,
) -> bool:
    file_path = Path(path)
    interval = max(0.2, float(interval))
    stable_seconds = max(0.0, float(stable_seconds))
    max_wait_seconds = max(stable_seconds, float(max_wait_seconds))
    try:
        if not file_path.exists() or file_path.stat().st_size <= 0:
            return False
    except OSError:
        return False
    if stable_seconds <= 0:
        return True

    last_size = -1
    stable_for = 0.0
    waited = 0.0
    while stable_for < stable_seconds:
        try:
            current_size = file_path.stat().st_size
        except OSError:
            return False
        if current_size == last_size and current_size > 0:
            stable_for += interval
            if stable_for >= stable_seconds:
                return True
        else:
            stable_for = 0.0
            last_size = current_size
        if waited >= max_wait_seconds:
            return False
        time.sleep(interval)
        waited += interval
    return True
