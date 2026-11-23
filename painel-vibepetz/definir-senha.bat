@echo off
cd /d "%~dp0"
title Painel VibePetz - senha
where py >nul 2>nul && (py painel.py definir-senha) || (python painel.py definir-senha)
echo.
pause
