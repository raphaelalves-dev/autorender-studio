@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

REM Mudar para o diretorio do script
cd /d "%~dp0"

echo ========================================
echo AutoRender - Instalacao de Dependencias
echo ========================================
echo.

REM Verificar se Python esta instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado!
    echo.
    echo Instale Python 3.11 ou superior de: https://www.python.org/downloads/
    echo Marque a opcao "Add Python to PATH" durante a instalacao.
    echo.
    pause
    exit /b 1
)

echo [1/4] Verificando Python...
python --version

echo [2/4] Criando ambiente virtual...
if exist ".venv" (
    echo      Ambiente virtual ja existe, pulando...
) else (
    python -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Falha ao criar ambiente virtual!
        pause
        exit /b 1
    )
    echo      Ambiente virtual criado com sucesso!
)

echo [3/4] Instalando dependencias no ambiente virtual...
echo      Instalando watchdog e pyinstaller...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo [ERRO] Falha ao instalar dependencias!
    pause
    exit /b 1
)

echo.
echo ========================================
echo INSTALACAO CONCLUIDA COM SUCESSO!
echo ========================================
echo.
echo Proximos passos:
echo   1. Execute TESTAR_SEM_HISTORICO.bat para testar o aplicativo
echo   2. Execute BUILD_EXE_PORTABLE.bat para criar o executavel
echo   3. Execute BUILD_INSTALLER_COM_SENHA.bat para criar o instalador
echo.
echo IMPORTANTE:
echo   - Certifique-se de ter FFmpeg em bin\
echo   - Certifique-se de ter os presets em preset\
echo.
pause
