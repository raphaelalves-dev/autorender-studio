@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

REM Mudar para o diretorio do script
cd /d "%~dp0"

echo ========================================
echo AutoRender - Teste Sem Historico
echo ========================================
echo.

REM Verificar se ambiente virtual existe
if not exist ".venv\Scripts\python.exe" (
    echo [ERRO] Ambiente virtual nao encontrado!
    echo Execute INSTALAR.bat primeiro.
    pause
    exit /b 1
)

echo [1/4] Usando ambiente virtual...

echo [2/4] Verificando FFmpeg...
if not exist "bin\ffmpeg.exe" (
    echo [AVISO] FFmpeg nao encontrado em bin\
    echo O programa pode nao funcionar corretamente.
    echo.
)

echo [3/4] Configurando modo sem historico...
REM Backup temporario do config
if exist "config\settings.json" (
    copy /y "config\settings.json" "config\settings.backup.tmp" >nul 2>&1
)

REM Criar/ajustar config temporario com history_enabled=false
.venv\Scripts\python.exe -c "import json; from pathlib import Path; p=Path('config/settings.json'); c=json.loads(p.read_text()) if p.exists() else {}; c['history_enabled']=False; p.write_text(json.dumps(c,indent=2))" 2>nul

echo [4/4] Iniciando AutoRender (modo teste - SEM HISTORICO)...
echo.
echo MODO: Historico desabilitado temporariamente
echo Pressione Ctrl+C para encerrar
echo.

.venv\Scripts\python.exe AutoRenderPreset_GUI.py

REM Restaurar config original
if exist "config\settings.backup.tmp" (
    move /y "config\settings.backup.tmp" "config\settings.json" >nul 2>&1
    echo.
    echo [INFO] Configuracao original restaurada
)

echo.
echo ========================================
echo Teste finalizado
echo ========================================
pause
