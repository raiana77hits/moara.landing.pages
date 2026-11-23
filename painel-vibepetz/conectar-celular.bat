@echo off
title Painel VibePetz - celular
set "TS=tailscale"
where tailscale >nul 2>nul || set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
"%TS%" version >nul 2>nul
if errorlevel 1 (
  echo Nao encontrei o Tailscale. Instale em https://tailscale.com/download e entre na sua conta.
  pause
  exit /b 1
)
rem --https=8443: a porta 443 ja e usada pelo outro painel; este fica em :8443
"%TS%" serve --bg --https=8443 8766
echo.
echo ------------------------------------------------------------
echo Endereco para abrir no celular (comeca com https):
"%TS%" serve status
echo ------------------------------------------------------------
echo No celular: Tailscale ligado, abra o endereco acima (termina com :8443) e some /conferir no final.
echo.
pause
