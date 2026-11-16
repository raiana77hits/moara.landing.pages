@echo off
cd /d "%~dp0"
title Painel de Estoque - senha
where py >nul 2>nul && (py painel.py definir-senha) || (python painel.py definir-senha)
echo.
pause
