@echo off
chcp 65001 >nul
echo Este roteiro antigo foi substituido pelo instalador unico sem senha.
call "%~dp0BUILD_INSTALLER.bat"
exit /b %errorlevel%
