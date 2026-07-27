from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class QuarantineResult:
    destination_folder: Path
    moved_files: tuple[Path, ...]
    manifest_path: Path


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    return path.with_name(f"{path.stem}_{uuid.uuid4().hex[:8]}{path.suffix}")


def quarantine_files(
    paths: Iterable[Path],
    source_folder: Path,
    config,
    *,
    failed_file: Path,
    reason: str,
    attempts: int,
) -> QuarantineResult:
    """Move um trabalho inválido para uma pasta local e registra sua origem."""
    day_folder = config.quarantine_path / datetime.now().strftime("%Y-%m-%d")
    destination_folder = day_folder / source_folder.name
    destination_folder.mkdir(parents=True, exist_ok=True)

    moved: list[tuple[Path, Path]] = []
    try:
        for source in paths:
            source = Path(source)
            if not source.exists():
                raise FileNotFoundError(f"Arquivo não encontrado para quarentena: {source}")
            try:
                relative = source.relative_to(source_folder)
            except ValueError:
                relative = Path(source.name)
            destination = _unique_destination(destination_folder / relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moved.append((source, destination))
    except OSError:
        for source, destination in reversed(moved):
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists() and not source.exists():
                    shutil.move(str(destination), str(source))
            except OSError:
                pass
        raise

    manifest_path = destination_folder / f"quarentena_{datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:8]}.json"
    payload = {
        "quarantined_at": datetime.now().astimezone().isoformat(),
        "source_folder": str(source_folder),
        "failed_file": str(failed_file),
        "reason": reason,
        "attempts": attempts,
        "moved_files": [
            {"original": str(source), "quarantine": str(destination)}
            for source, destination in moved
        ],
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return QuarantineResult(
        destination_folder=destination_folder,
        moved_files=tuple(destination for _source, destination in moved),
        manifest_path=manifest_path,
    )
