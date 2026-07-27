from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from backend.config import (
    AppConfig,
    DEFAULT_SPECIAL_CYCLE_FOLDERS,
    DOWNLOADER_MANUAL_CYCLE_SUFFIX,
    is_recognized_cycle_folder_name,
)
from backend.history import RenderHistory
from backend.queue_manager import is_video_file


@dataclass(frozen=True)
class CandidateVideoGroup:
    paths: tuple[Path, ...]

    @property
    def primary_path(self) -> Path:
        return self.paths[0]

    @property
    def is_pair(self) -> bool:
        return len(self.paths) == 2


def _normalized_folder_name(value: str) -> str:
    return value.strip().lower()


def special_cycle_names(config: AppConfig) -> set[str]:
    names = getattr(config, "special_cycle_folders", None) or DEFAULT_SPECIAL_CYCLE_FOLDERS
    return {_normalized_folder_name(str(name)) for name in names if str(name).strip()}


def is_auto_cycle_folder(path: Path) -> bool:
    return path.is_dir() and path.name.upper().endswith("_AUTO")


def is_downloader_manual_cycle_folder(path: Path) -> bool:
    name = path.name.upper()
    return path.is_dir() and (
        name == DOWNLOADER_MANUAL_CYCLE_SUFFIX.lstrip("_")
        or name.endswith(DOWNLOADER_MANUAL_CYCLE_SUFFIX)
    )


def is_special_cycle_folder(path: Path, config: AppConfig) -> bool:
    return path.is_dir() and _normalized_folder_name(path.name) in special_cycle_names(config)


def is_cycle_like_folder(path: Path, config: AppConfig) -> bool:
    """Pastas que representam um lote/ciclo renderizável dentro de entrada."""
    return path.is_dir() and is_recognized_cycle_folder_name(path.name, config.special_cycle_folders)


def direct_cycle_folders(input_dir: Path, config: AppConfig) -> List[Path]:
    if not input_dir.exists():
        return []

    try:
        folders = [p for p in input_dir.iterdir() if is_cycle_like_folder(p, config)]
    except OSError:
        return []

    def sort_key(path: Path):
        # Mantém ciclos dos downloaders antes das pastas manuais configuradas.
        priority = 0 if (is_auto_cycle_folder(path) or is_downloader_manual_cycle_folder(path)) else 1
        return (priority, path.name.lower())

    return sorted(folders, key=sort_key)


def direct_auto_cycle_folders(input_dir: Path) -> List[Path]:
    """Compatibilidade com código antigo: retorna apenas pastas *_AUTO."""
    if not input_dir.exists():
        return []
    try:
        return sorted([p for p in input_dir.iterdir() if is_auto_cycle_folder(p)], key=lambda p: p.name.lower())
    except OSError:
        return []


def video_files_in_folder(folder: Path, recursive: bool = True) -> List[Path]:
    try:
        iterator = folder.rglob("*") if recursive else folder.iterdir()
        return sorted([p for p in iterator if is_video_file(p)], key=lambda p: str(p).lower())
    except OSError:
        return []


def loose_video_files(input_dir: Path) -> List[Path]:
    if not input_dir.exists():
        return []
    try:
        return sorted([p for p in input_dir.iterdir() if is_video_file(p)], key=lambda p: p.name.lower())
    except OSError:
        return []


def source_mode_for_path(path: Path, config: AppConfig) -> str:
    cycle = cycle_folder_for_path(path, config)
    if not cycle:
        return "loose"
    return "cycle" if (is_auto_cycle_folder(cycle) or is_downloader_manual_cycle_folder(cycle)) else "manual_retry"


def cycle_folder_for_path(path: Path, config: AppConfig) -> Optional[Path]:
    try:
        rel = path.resolve().relative_to(config.input_path.resolve())
    except ValueError:
        return None
    if len(rel.parts) < 2:
        return None
    candidate = config.input_path / rel.parts[0]
    return candidate if is_cycle_like_folder(candidate, config) else None


def has_active_auto_cycle(config: AppConfig, history: Optional[RenderHistory] = None) -> bool:
    for folder in direct_auto_cycle_folders(config.input_path):
        if not video_files_in_folder(folder, recursive=config.recursive_input):
            continue
        if history and history.is_cycle_done(folder):
            continue
        return True
    return False


def has_active_cycle_folder(config: AppConfig, history: Optional[RenderHistory] = None) -> bool:
    for folder in direct_cycle_folders(config.input_path, config):
        if not video_files_in_folder(folder, recursive=config.recursive_input):
            continue
        if history and history.is_cycle_done(folder):
            continue
        return True
    return False


def _candidate_videos(
    config: AppConfig,
    history: Optional[RenderHistory] = None,
    *,
    skip_done_videos: bool = True,
) -> List[Path]:
    """
    Regra operacional:
    1. Se existir pasta de ciclo ativa, processa somente vídeos dentro dessas pastas.
       Pastas de ciclo: *_AUTO, *_MANUAL_TODAS e pastas especiais como Manual ou Manual_Retry.
    2. Se não existir nenhuma pasta de ciclo ativa, processa vídeos soltos na raiz de entrada.
    3. Histórico remove vídeos/ciclos já renderizados da lista.
    """
    input_dir = config.input_path
    input_dir.mkdir(parents=True, exist_ok=True)

    cycle_folders = direct_cycle_folders(input_dir, config)
    folders_with_videos = [
        (folder, video_files_in_folder(folder, recursive=config.recursive_input))
        for folder in cycle_folders
    ]
    active_folders = [
        (folder, videos)
        for folder, videos in folders_with_videos
        if videos and not (history and history.is_cycle_done(folder))
    ]

    candidates: List[Path] = []
    if active_folders:
        for _folder, videos in active_folders:
            for video in videos:
                if skip_done_videos and history and history.is_video_done(video):
                    continue
                candidates.append(video)
        return candidates

    for video in loose_video_files(input_dir):
        if skip_done_videos and history and history.is_video_done(video):
            continue
        candidates.append(video)
    return candidates


def candidate_videos(config: AppConfig, history: Optional[RenderHistory] = None) -> List[Path]:
    return _candidate_videos(config, history, skip_done_videos=True)


def candidate_all_videos(config: AppConfig, history: Optional[RenderHistory] = None) -> List[Path]:
    """Fila individual: renderiza cada vídeo elegível com o preset simples.

    Este seletor não forma pares. Assim, um lote contendo 81... até 86...
    produz uma saída independente para cada arquivo presente.
    """
    return candidate_videos(config, history)


def all_input_videos(config: AppConfig) -> List[Path]:
    """Lista todos os vídeos conhecidos na entrada, inclusive em ciclos concluídos."""
    videos = loose_video_files(config.input_path)
    for folder in direct_cycle_folders(config.input_path, config):
        videos.extend(video_files_in_folder(folder, recursive=config.recursive_input))
    return sorted(set(videos), key=lambda path: str(path).lower())


def _pair_prefix(path: Path) -> Optional[str]:
    stem = path.stem.strip()
    if stem.startswith("81"):
        return "81"
    if stem.startswith("82"):
        return "82"
    return None


def candidate_video_groups(config: AppConfig, history: Optional[RenderHistory] = None) -> List[CandidateVideoGroup]:
    """Agrupa automaticamente arquivos 81... + 82... da mesma pasta.

    Arquivos que começam com 81 ou 82 só entram na fila quando o par existir.
    Outros vídeos continuam como renders individuais.
    """
    videos = _candidate_videos(config, history, skip_done_videos=False)
    by_parent: dict[Path, dict[str, list[Path]]] = {}
    paired: set[Path] = set()
    groups: list[CandidateVideoGroup] = []

    for video in videos:
        prefix = _pair_prefix(video)
        if not prefix:
            continue
        parent_groups = by_parent.setdefault(video.parent, {"81": [], "82": []})
        parent_groups[prefix].append(video)

    for parent in sorted(by_parent, key=lambda p: str(p).lower()):
        items = by_parent[parent]
        starts_81 = sorted(items["81"], key=lambda p: p.name.lower())
        starts_82 = sorted(items["82"], key=lambda p: p.name.lower())
        for video_81, video_82 in zip(starts_81, starts_82):
            paths = (video_81, video_82)
            if history and all(history.is_video_done(path) for path in paths):
                paired.update(paths)
                continue
            groups.append(CandidateVideoGroup(paths))
            paired.update(paths)

    for video in videos:
        if video in paired:
            continue
        if _pair_prefix(video):
            continue
        if history and history.is_video_done(video):
            continue
        groups.append(CandidateVideoGroup((video,)))

    return sorted(groups, key=lambda group: str(group.primary_path).lower())


def candidate_manual_82_videos(config: AppConfig, history: Optional[RenderHistory] = None) -> List[Path]:
    """Fila manual deste layout: renderiza somente a câmera 82."""
    return [video for video in _candidate_videos(config, history, skip_done_videos=True) if _pair_prefix(video) == "82"]


def next_candidate_video(config: AppConfig, history: Optional[RenderHistory] = None) -> Optional[Path]:
    videos = candidate_videos(config, history)
    return videos[0] if videos else None


def next_candidate_video_group(config: AppConfig, history: Optional[RenderHistory] = None) -> Optional[CandidateVideoGroup]:
    groups = candidate_video_groups(config, history)
    return groups[0] if groups else None


def next_manual_82_video(config: AppConfig, history: Optional[RenderHistory] = None) -> Optional[Path]:
    videos = candidate_manual_82_videos(config, history)
    return videos[0] if videos else None


def count_cycle_videos(folder: Path, recursive: bool = True) -> int:
    return len(video_files_in_folder(folder, recursive=recursive))


def remaining_unprocessed_in_cycle(folder: Path, history: RenderHistory, recursive: bool = True) -> List[Path]:
    return [p for p in video_files_in_folder(folder, recursive=recursive) if not history.is_video_done(p)]
