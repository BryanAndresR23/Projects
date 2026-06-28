@echo off
REM ============================================================
REM   Lanzador - Panel Conciliacion BCE vs MEF
REM   Instala dependencias si faltan y abre la app en el navegador.
REM   Deja este .bat en la MISMA carpeta que conciliacion_app.py
REM   y Conciliacion.py.
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
title Panel Conciliacion BCE vs MEF

REM --- Buscar Python (python o py) ---
set "PYEXE=python"
where python >nul 2>nul || set "PYEXE=py"

echo ============================================================
echo   Verificando dependencias (flask, xlrd, openpyxl)...
echo ============================================================
%PYEXE% -c "import flask, xlrd, openpyxl, xlutils, xlwt" 2>nul
if errorlevel 1 (
    echo   Faltan dependencias. Instalando...
    %PYEXE% -m pip install --upgrade pip
    %PYEXE% -m pip install flask xlrd openpyxl xlutils xlwt
)

REM --- Verificar que esten los dos archivos del proyecto ---
if not exist "conciliacion_app.py" (
    echo.
    echo   ERROR: no encuentro conciliacion_app.py en esta carpeta.
    echo   Copia conciliacion_app.py y Conciliacion.py juntos aqui.
    pause
    exit /b 1
)
if not exist "Conciliacion.py" (
    echo.
    echo   ERROR: falta Conciliacion.py ^(la logica del cruce^).
    echo   Debe estar en la MISMA carpeta que conciliacion_app.py.
    pause
    exit /b 1
)

REM --- Recordatorio del logo oficial del BCE (opcional) ---
if not exist "logo_bce.png" if not exist "logo_bce.jpg" if not exist "logo_bce.svg" (
    echo.
    echo   NOTA: para mostrar el logo OFICIAL del BCE, guarda la imagen como
    echo         "logo_bce.png" en esta misma carpeta. Si no esta, se usa el
    echo         emblema por defecto. No es obligatorio.
)

echo.
echo   Iniciando panel...  URL: http://127.0.0.1:5001/
echo   (Para cerrar: cierra esta ventana o pulsa Ctrl+C)
echo ============================================================
%PYEXE% conciliacion_app.py

pause
