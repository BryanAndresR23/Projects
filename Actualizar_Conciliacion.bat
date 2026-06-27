@echo off
REM ============================================================
REM   Actualizar Conciliacion BCE vs MEF desde GitHub
REM   - Si la carpeta YA es un clon del repo: baja lo ultimo (git pull).
REM   - Si NO lo es: clona el repo aqui la primera vez.
REM   Doble clic cada vez que quieras la version mas reciente.
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
title Actualizar Conciliacion BCE vs MEF

set "REPO=https://github.com/BryanAndresR23/Projects.git"
set "RAMA=claude/brave-noether-o44tpf"

REM --- Verificar que Git este instalado ---
where git >nul 2>nul
if errorlevel 1 (
    echo.
    echo   ERROR: Git no esta instalado.
    echo   Descargalo una sola vez desde: https://git-scm.com/download/win
    echo   Instala con las opciones por defecto y vuelve a dar doble clic aqui.
    echo.
    pause
    exit /b 1
)

if exist ".git" (
    echo ============================================================
    echo   Bajando ultima version ^(rama %RAMA%^)...
    echo ============================================================
    git checkout %RAMA%
    git pull origin %RAMA%
) else (
    echo ============================================================
    echo   Primera vez: clonando el repositorio aqui...
    echo ============================================================
    git clone --branch %RAMA% %REPO% repo_conciliacion
    echo.
    echo   Listo. Tus archivos quedaron en la subcarpeta: repo_conciliacion
    echo   Abre esa carpeta y usa Iniciar_Conciliacion.bat
)

echo.
echo   ============================================================
echo   Actualizacion terminada.
echo   ============================================================
pause
