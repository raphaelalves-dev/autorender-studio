@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0BUILD_INSTALLER.ps1"
if errorlevel 1 (
  echo Falha ao criar o instalador. Consulte a mensagem acima.
  exit /b 1
)
echo Instalador pronto na pasta output.
exit /b 0
