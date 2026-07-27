@echo off
chcp 65001 > nul
setlocal

cd /d "%~dp0"

echo ========================================
echo AutoRender Studio - Criar EXE portavel
echo ========================================
echo.

if /i "%~1"=="--check" (
    set "CHECK_ONLY=1"
) else (
    set "CHECK_ONLY="
)

if not exist "AutoRenderPreset_GUI.py" (
    echo [ERRO] AutoRenderPreset_GUI.py nao encontrado.
    goto fail
)

if not exist "AutoRenderPreset.spec" (
    echo [ERRO] AutoRenderPreset.spec nao encontrado.
    goto fail
)

if not exist "config\settings.json" (
    echo [ERRO] config\settings.json nao encontrado.
    goto fail
)

if not exist "bin\ffmpeg.exe" (
    echo [ERRO] bin\ffmpeg.exe nao encontrado.
    goto fail
)

if not exist "bin\ffprobe.exe" (
    echo [ERRO] bin\ffprobe.exe nao encontrado.
    goto fail
)

if not exist "preset\*.mov" (
    echo [ERRO] Nenhum preset .mov encontrado na pasta preset.
    goto fail
)

if defined CHECK_ONLY (
    echo [OK] Arquivos principais encontrados.
    goto done
)

echo [1/7] Preparando Python...
if not exist ".venv\Scripts\python.exe" (
    echo      Ambiente virtual nao encontrado. Criando .venv...
    py -3 -m venv ".venv" 2>nul
    if errorlevel 1 (
        python -m venv ".venv"
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo [ERRO] Nao foi possivel criar ou encontrar .venv\Scripts\python.exe.
    goto fail
)

set "PYTHON_EXE=.venv\Scripts\python.exe"

echo [2/7] Instalando dependencias de build...
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 goto fail

"%PYTHON_EXE%" -m pip install -r requirements.txt "pyinstaller>=6.0.0"
if errorlevel 1 goto fail

echo [3/7] Limpando build anterior...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

echo [4/7] Criando executavel com PyInstaller...
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean "AutoRenderPreset.spec"
if errorlevel 1 (
    echo [ERRO] PyInstaller falhou.
    goto fail
)

if not exist "dist\AutoRenderPreset\AutoRenderPreset.exe" (
    echo [ERRO] dist\AutoRenderPreset\AutoRenderPreset.exe nao foi criado.
    goto fail
)

echo [5/7] Copiando config, FFmpeg e presets para a pasta do EXE...
robocopy "config" "dist\AutoRenderPreset\config" /E /XD "__pycache__" /XF "*.pyc" "*.pyo" > nul
if errorlevel 8 goto robocopy_fail

robocopy "bin" "dist\AutoRenderPreset\bin" /E > nul
if errorlevel 8 goto robocopy_fail

robocopy "preset" "dist\AutoRenderPreset\preset" /E > nul
if errorlevel 8 goto robocopy_fail

echo [6/7] Criando pastas de trabalho...
for %%D in (entrada saida processados erros logs update) do (
    if not exist "dist\AutoRenderPreset\%%D" mkdir "dist\AutoRenderPreset\%%D"
    if not exist "dist\AutoRenderPreset\%%D\.gitkeep" type nul > "dist\AutoRenderPreset\%%D\.gitkeep"
)

echo [7/7] Copiando documentos auxiliares...
if exist "README.md" copy /y "README.md" "dist\AutoRenderPreset\" > nul
if exist "requirements.txt" copy /y "requirements.txt" "dist\AutoRenderPreset\" > nul
if exist "autorendericon.ico" copy /y "autorendericon.ico" "dist\AutoRenderPreset\" > nul

echo.
echo ========================================
echo EXE PORTAVEL CRIADO COM SUCESSO
echo ========================================
echo Pasta: dist\AutoRenderPreset
echo EXE:   dist\AutoRenderPreset\AutoRenderPreset.exe
echo.
goto done

:robocopy_fail
echo [ERRO] Falha ao copiar arquivos com robocopy.
goto fail

:fail
echo.
echo Build do EXE falhou.
if not defined AUTORENDER_NO_PAUSE pause
exit /b 1

:done
if not defined AUTORENDER_NO_PAUSE pause
exit /b 0
