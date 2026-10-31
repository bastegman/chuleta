@echo off
rem Instala Chuleta en este PC: Python (si falta), librerias y acceso directo. Sin permisos de administrador.
chcp 65001 >nul
title Instalar Chuleta
rem Con escritorio abre su propia ventana y esconde esta consola; solo se para aqui si algo fallo (-Consola: todo por consola).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_app\instalar.ps1" %*
if errorlevel 1 (
  echo.
  pause
)
