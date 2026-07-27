@echo off
chcp 65001 > nul
setlocal

cd /d "%~dp0"

echo ========================================
echo AutoRender Studio - EXE + instalador
echo ========================================
echo.

if /i "%~1"=="--check" (
    set "CHECK_ONLY=1"
) else (
    set "CHECK_ONLY="
)

if not exist "AutoRenderStudio_Setup.iss" (
    echo [ERRO] AutoRenderStudio_Setup.iss nao encontrado.
    goto fail
)

if not exist "BUILD_EXE_PORTABLE.bat" (
    echo [ERRO] BUILD_EXE_PORTABLE.bat nao encontrado.
    goto fail
)

echo [1/6] Verificando Inno Setup...
call :find_inno
if not defined INNO_PATH (
    echo [ERRO] Inno Setup nao encontrado.
    echo Instale o Inno Setup 7 ou 6 e rode este arquivo novamente.
    echo Site: https://jrsoftware.org/isinfo.php
    goto fail
)
echo      Encontrado: %INNO_PATH%

echo [2/6] Verificando arquivos do EXE...
set "AUTORENDER_NO_PAUSE=1"
call "BUILD_EXE_PORTABLE.bat" --check
if errorlevel 1 goto fail

if defined CHECK_ONLY (
    echo.
    echo [OK] Checagem concluida. Nada foi compilado.
    goto done
)

echo [3/6] Criando EXE portavel...
call "BUILD_EXE_PORTABLE.bat"
if errorlevel 1 goto fail

if not exist "dist\AutoRenderPreset\AutoRenderPreset.exe" (
    echo [ERRO] EXE nao encontrado depois do build.
    goto fail
)

echo [4/6] Pedindo senha do instalador...
set "AUTORENDER_SETUP_PASSWORD="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "$p=Read-Host 'Digite a senha do instalador' -AsSecureString; $b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($p); try {[Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)} finally {[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b)}"`) do set "AUTORENDER_SETUP_PASSWORD=%%P"

if not defined AUTORENDER_SETUP_PASSWORD (
    echo [ERRO] Senha vazia. O instalador nao sera criado sem senha.
    goto fail
)

echo [5/6] Limpando saida anterior...
if not exist "output" mkdir "output"
del /q "output\AutoRenderStudio_Setup_*.exe" 2>nul
del /q "output\AutoRenderStudio_Setup_*.bin" 2>nul

echo [6/6] Compilando instalador com senha...
echo      Os presets sao grandes; esta etapa pode demorar.
"%INNO_PATH%" "AutoRenderStudio_Setup.iss"
if errorlevel 1 (
    echo [ERRO] Inno Setup falhou.
    goto fail
)

if not exist "output\AutoRenderStudio_Setup_*.exe" (
    echo [ERRO] Instalador nao foi gerado na pasta output.
    goto fail
)

echo.
echo ========================================
echo INSTALADOR CRIADO COM SUCESSO
echo ========================================
echo Arquivos gerados:
dir /b "output\AutoRenderStudio_Setup_*"
echo.
echo Importante:
echo - Por causa dos presets grandes, o Inno pode gerar .exe + arquivos .bin.
echo - Para distribuir, envie TODOS os arquivos AutoRenderStudio_Setup_* da pasta output.
echo - A senha usada foi a senha digitada nesta execucao.
echo.
goto done

:find_inno
set "INNO_PATH="
if exist "C:\Program Files\Inno Setup 7\ISCC.exe" set "INNO_PATH=C:\Program Files\Inno Setup 7\ISCC.exe"
if not defined INNO_PATH if exist "C:\Program Files (x86)\Inno Setup 7\ISCC.exe" set "INNO_PATH=C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
if not defined INNO_PATH if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "INNO_PATH=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not defined INNO_PATH if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "INNO_PATH=C:\Program Files\Inno Setup 6\ISCC.exe"
if not defined INNO_PATH if exist "C:\Program Files (x86)\Inno Setup 5\ISCC.exe" set "INNO_PATH=C:\Program Files (x86)\Inno Setup 5\ISCC.exe"
if not defined INNO_PATH for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do if not defined INNO_PATH set "INNO_PATH=%%I"
exit /b 0

:fail
echo.
echo Processo interrompido.
if not defined AUTORENDER_NO_PAUSE pause
exit /b 1

:done
if not defined AUTORENDER_NO_PAUSE pause
exit /b 0
