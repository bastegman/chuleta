@echo off
rem Instala Notas en este PC: Python (si falta), librerias y acceso directo. Sin permisos de administrador.
chcp 65001 >nul
title Instalar Notas
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_app\instalar.ps1"
echo.
pause
