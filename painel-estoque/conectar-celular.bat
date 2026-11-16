@echo off
title Painel de Estoque - celular
set "TS=tailscale"
where tailscale >nul 2>nul || set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
"%TS%" version >nul 2>nul
if errorlevel 1 (
  echo Nao encontrei o Tailscale. Instale em https://tailscale.com/download e entre na sua conta.
  pause
  exit /b 1
)
"%TS%" serve --bg 8765
echo.
echo ------------------------------------------------------------
echo Endereco para abrir no celular (comeca com https):
"%TS%" serve status
echo ------------------------------------------------------------
echo No celular: Tailscale ligado, abra o endereco acima e some /conferir no final.
echo.
pause
