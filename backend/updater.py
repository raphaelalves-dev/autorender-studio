from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from backend.app_info import APP_VERSION
from backend.install_history import InstallationHistoryError, snapshot_before_update


MAX_UPDATE_ZIP_BYTES = 750 * 1024 * 1024
MAX_UPDATE_UNCOMPRESSED_BYTES = 1500 * 1024 * 1024
DATA_DIRS = {"entrada", "saida", "processados", "erros", "quarentena", "logs", "preset", "update", "render_staging"}


class UpdatePackageError(RuntimeError):
    pass


@dataclass
class PreparedUpdate:
    source_zip: str
    updates_dir: str
    pending_dir: str
    apply_script: str
    file_count: int
    total_size_bytes: int
    history_entry: str


def _safe_member_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or any(part in ("..", "") or ":" in part for part in path.parts):
        raise UpdatePackageError(f"Caminho inseguro dentro do ZIP: {name}")
    return path


def _payload_root(pending_dir: Path) -> Path:
    candidates = [
        path.parent for path in pending_dir.rglob("AutoRenderPreset.exe")
        if len(path.relative_to(pending_dir).parts) <= 4
        and (path.parent / "_internal" / "base_library.zip").is_file()
    ]
    if len(candidates) != 1:
        raise UpdatePackageError("O ZIP deve conter um AutoRenderPreset.exe e a pasta _internal completa.")
    return candidates[0]


def _write_apply_script(pending_dir: Path, payload_dir: Path, script_path: Path, history_entry: Path) -> None:
    excludes = " ".join(sorted(DATA_DIRS))
    payload_suffix = str(payload_dir.relative_to(pending_dir)).replace("/", "\\")
    source = f"%UPDATES_DIR%{pending_dir.name}"
    if payload_suffix != ".":
        source += f"\\{payload_suffix}"
    restore_script = f"%UPDATES_DIR%history\\{history_entry.name}\\RESTAURAR.bat"
    script = f"""@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "UPDATES_DIR=%~dp0"
for %%I in ("%~dp0..") do set "APP_DIR=%%~fI"
set "SRC={source}"

echo ================================================
echo  AutoRender Studio - Aplicar atualizacao automatica
echo ================================================
echo.
echo Aguardando o AutoRender Studio fechar...
echo.

for /L %%I in (1,1,30) do (
  tasklist /FI "IMAGENAME eq AutoRenderPreset.exe" | find /I "AutoRenderPreset.exe" >nul
  if errorlevel 1 goto :apply_update
  timeout /t 1 /nobreak >nul
)
echo ERRO: o AutoRender Studio ainda esta aberto. Atualizacao cancelada.
exit /b 1

:apply_update
echo Aplicando atualizacao.
echo A atualizacao nao substitui pastas de dados: {excludes}
echo.

if not exist "%SRC%\\AutoRenderPreset.exe" goto :rollback
if not exist "%SRC%\\_internal\\base_library.zip" goto :rollback

robocopy "%SRC%" "%APP_DIR%" /E /XD _internal {excludes} /XF settings.json render_history.json *.log *.jsonl /R:1 /W:1 >nul
if errorlevel 8 goto :rollback
robocopy "%SRC%\\_internal" "%APP_DIR%\\_internal" /MIR /R:1 /W:1 >nul
if errorlevel 8 goto :rollback
echo aplicado>"%UPDATES_DIR%history\\{history_entry.name}\\estado.txt"

echo.
echo Atualizacao aplicada.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$desktop=[Environment]::GetFolderPath('Desktop'); $lnk=Join-Path $desktop 'AutoRender Studio.lnk'; $shell=New-Object -ComObject WScript.Shell; $shortcut=$shell.CreateShortcut($lnk); $shortcut.TargetPath=(Join-Path $env:APP_DIR 'AutoRenderPreset.exe'); $shortcut.WorkingDirectory=$env:APP_DIR; $icon=(Join-Path $env:APP_DIR 'autorendericon.ico'); if(Test-Path -LiteralPath $icon){{$shortcut.IconLocation=$icon}}; $shortcut.Save()" >nul 2>nul
if not defined AUTORENDER_UPDATE_NO_RESTART if exist "%APP_DIR%\\AutoRenderPreset.exe" start "" "%APP_DIR%\\AutoRenderPreset.exe"
exit /b 0

:rollback
echo ERRO: falha ao aplicar a atualizacao. Restaurando a versao anterior...
call "{restore_script}"
if errorlevel 1 echo Falha na restauracao automatica. Execute update\\RESTAURAR_VERSAO_ANTERIOR.bat manualmente.
exit /b 1
"""
    script_path.write_text(script, encoding="utf-8")


def prepare_update_zip(zip_path: str | Path, project_root: str | Path) -> PreparedUpdate:
    source = Path(zip_path).expanduser().resolve()
    root = Path(project_root).resolve()
    if not source.exists() or not source.is_file():
        raise UpdatePackageError(f"ZIP de atualizacao nao encontrado: {source}")
    if source.suffix.lower() != ".zip":
        raise UpdatePackageError("Selecione um arquivo .zip valido.")
    if source.stat().st_size > MAX_UPDATE_ZIP_BYTES:
        limit_mb = MAX_UPDATE_ZIP_BYTES // (1024 * 1024)
        raise UpdatePackageError(f"ZIP muito grande para atualizacao segura. Limite: {limit_mb} MB.")
    if not zipfile.is_zipfile(source):
        raise UpdatePackageError("Arquivo selecionado nao e um ZIP valido.")

    updates_dir = root / "update"
    pending_dir = updates_dir / "pending"
    archive_dir = updates_dir / "packages"
    updates_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archived_zip = archive_dir / f"update_{stamp}_{source.name}"
    shutil.copy2(source, archived_zip)

    if pending_dir.exists():
        shutil.rmtree(pending_dir)
    pending_dir.mkdir(parents=True, exist_ok=True)

    file_count = 0
    total_size = 0
    try:
        with zipfile.ZipFile(source) as zf:
            for info in zf.infolist():
                member = _safe_member_path(info.filename)
                if info.is_dir():
                    continue
                total_size += int(info.file_size)
                if total_size > MAX_UPDATE_UNCOMPRESSED_BYTES:
                    limit_mb = MAX_UPDATE_UNCOMPRESSED_BYTES // (1024 * 1024)
                    raise UpdatePackageError(f"Conteudo descompactado muito grande. Limite: {limit_mb} MB.")
                target = pending_dir / Path(*member.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                file_count += 1

        if file_count == 0:
            raise UpdatePackageError("ZIP de atualizacao esta vazio.")
        payload_dir = _payload_root(pending_dir)
    except Exception:
        if pending_dir.exists():
            shutil.rmtree(pending_dir)
        raise

    try:
        history_entry = snapshot_before_update(root, APP_VERSION, source.name)
    except InstallationHistoryError as exc:
        raise UpdatePackageError(str(exc)) from exc

    apply_script = updates_dir / "APLICAR_ATUALIZACAO.bat"
    _write_apply_script(pending_dir, payload_dir, apply_script, history_entry)
    return PreparedUpdate(
        source_zip=str(archived_zip),
        updates_dir=str(updates_dir),
        pending_dir=str(pending_dir),
        apply_script=str(apply_script),
        file_count=file_count,
        total_size_bytes=total_size,
        history_entry=str(history_entry),
    )
