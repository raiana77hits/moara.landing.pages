@echo off
cd /d "%~dp0"
title Painel de Estoque (iniciado com o Windows)
where py >nul 2>nul && (py painel.py --sem-navegador) || (python painel.py --sem-navegador)
if errorlevel 1 (
  echo.
  echo Nao consegui abrir o painel. Confira se o Python esta instalado.
  pause
)
