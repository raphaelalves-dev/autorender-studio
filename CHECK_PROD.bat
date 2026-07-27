@echo off
chcp 65001 > nul
setlocal

cd /d "%~dp0"
set "CHECK_PROD_NO_PAUSE=%AUTORENDER_NO_PAUSE%"

echo ========================================
echo AutoRender Studio - Checagem de producao
echo ========================================
echo.

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

echo [1/3] Validando arquivos Python...
"%PYTHON_EXE%" -B -c "import ast, pathlib; files=[pathlib.Path('AutoRenderPreset_GUI.py'), *pathlib.Path('backend').glob('*.py'), *pathlib.Path('frontend').glob('*.py')]; [ast.parse(p.read_text(encoding='utf-8-sig'), filename=str(p)) for p in files]; print('OK: sintaxe valida')"
if errorlevel 1 goto fail

echo.
echo [2/3] Rodando diagnostico do app...
"%PYTHON_EXE%" -B "AutoRenderPreset_GUI.py" doctor
if errorlevel 1 goto fail

echo.
echo [3/3] Checando build do instalador...
set "AUTORENDER_NO_PAUSE=1"
call "BUILD_INSTALLER_COM_SENHA.bat" --check
set "INSTALLER_CHECK_RESULT=%ERRORLEVEL%"
set "AUTORENDER_NO_PAUSE=%CHECK_PROD_NO_PAUSE%"
if not "%INSTALLER_CHECK_RESULT%"=="0" goto fail

echo.
echo ========================================
echo CHECAGEM DE PRODUCAO OK
echo ========================================
goto done

:fail
echo.
echo Checagem de producao falhou.
if not defined CHECK_PROD_NO_PAUSE pause
exit /b 1

:done
if not defined CHECK_PROD_NO_PAUSE pause
exit /b 0
