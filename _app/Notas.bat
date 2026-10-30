@echo off
rem Lanzador de Notas (lo usa el acceso directo "Notas" del escritorio).
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title Notas - servidor (cierra esta ventana para apagar la app)
cd /d "%~dp0"
rem python.txt lo escribe INSTALAR.bat: el Python en el que se instalaron las librerias
set "PY="
if exist "%~dp0python.txt" set /p PY=<"%~dp0python.txt"
if defined PY if exist "%PY%" goto run
set "PY=python"
where py >nul 2>nul && set "PY=py"
:run
"%PY%" server.py %*
if errorlevel 1 (
  echo.
  echo Algo ha fallado al arrancar. Copia el mensaje de arriba para revisarlo.
  pause
)
