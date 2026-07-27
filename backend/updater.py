from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath


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


def _safe_member_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/").strip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or any(part in ("..", "") for part in path.parts):
        raise UpdatePackageError(f"Caminho inseguro dentro do ZIP: {name}")
    return path


def _write_apply_script(project_root: Path, pending_dir: Path, script_path: Path) -> None:
    excludes = " ".join(DATA_DIRS)
    script = f"""@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "UPDATES_DIR=%~dp0"
for %%I in ("%~dp0..") do set "APP_DIR=%%~fI"
set "SRC=%UPDATES_DIR%{pending_dir.name}"
if exist "%SRC%\\AutoRenderPreset" set "SRC=%SRC%\\AutoRenderPreset"
if exist "%SRC%\\AutoRenderStudio" set "SRC=%SRC%\\AutoRenderStudio"
rem Alguns compactadores adicionam uma pasta com o nome da versao por fora.
rem Procura o executavel ate dois niveis abaixo antes de aplicar o pacote.
if not exist "%SRC%\\AutoRenderPreset.exe" (
  for /d %%D in ("%SRC%\\*") do (
    if exist "%%~fD\\AutoRenderPreset.exe" set "SRC=%%~fD"
    if exist "%%~fD\\AutoRenderPreset\\AutoRenderPreset.exe" set "SRC=%%~fD\\AutoRenderPreset"
    if exist "%%~fD\\AutoRenderStudio\\AutoRenderPreset.exe" set "SRC=%%~fD\\AutoRenderStudio"
  )
)

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

:apply_update
echo Aplicando atualizacao.
echo A atualizacao nao substitui pastas de dados: {excludes}
echo.

if not exist "%SRC%\\AutoRenderPreset.exe" (
  echo ERRO: AutoRenderPreset.exe nao foi encontrado dentro do pacote.
  pause
  exit /b 1
)

robocopy "%SRC%" "%APP_DIR%" /E /XD {excludes} /XF settings.json render_history.json *.log *.jsonl /R:1 /W:1
if errorlevel 8 (
  echo.
  echo ERRO: falha ao aplicar atualizacao.
  pause
  exit /b 1
)

echo.
echo Atualizacao aplicada.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$desktop=[Environment]::GetFolderPath('Desktop'); $lnk=Join-Path $desktop 'AutoRender Studio.lnk'; $shell=New-Object -ComObject WScript.Shell; $shortcut=$shell.CreateShortcut($lnk); $shortcut.TargetPath=(Join-Path $env:APP_DIR 'AutoRenderPreset.exe'); $shortcut.WorkingDirectory=$env:APP_DIR; $icon=(Join-Path $env:APP_DIR 'autorendericon.ico'); if(Test-Path -LiteralPath $icon){{$shortcut.IconLocation=$icon}}; $shortcut.Save()" >nul 2>nul
if exist "%APP_DIR%\\AutoRenderPreset.exe" start "" "%APP_DIR%\\AutoRenderPreset.exe"
exit /b 0
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
    except Exception:
        if pending_dir.exists():
            shutil.rmtree(pending_dir)
        raise

    apply_script = updates_dir / "APLICAR_ATUALIZACAO.bat"
    _write_apply_script(root, pending_dir, apply_script)
    return PreparedUpdate(
        source_zip=str(archived_zip),
        updates_dir=str(updates_dir),
        pending_dir=str(pending_dir),
        apply_script=str(apply_script),
        file_count=file_count,
        total_size_bytes=total_size,
    )
