@echo off
cd /d "%~dp0"
title Painel de Estoque
where py >nul 2>nul && (py painel.py) || (python painel.py)
if errorlevel 1 (
  echo.
  echo Nao consegui abrir o painel. Confira se o Python esta instalado: https://www.python.org/downloads/
  pause
)
