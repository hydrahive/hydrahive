@echo off
:: ─────────────────────────────────────────────────────────────────────────────
:: HydraHive Tailscale Connect (Windows)
::
:: Verbindet diesen PC mit dem HydraHive Tailnet.
:: Usage: tailscale-connect.bat tskey-auth-xxxxx
:: ─────────────────────────────────────────────────────────────────────────────

set TAILSCALE="C:\Program Files\Tailscale\tailscale.exe"

if "%~1"=="" (
    echo.
    echo  HydraHive Tailscale Connect
    echo  ───────────────────────────
    echo.
    echo  Usage: tailscale-connect.bat ^<AUTH-KEY^>
    echo.
    echo  Den Auth-Key bekommst du vom HydraHive-Admin.
    echo  Er beginnt mit: tskey-auth-...
    echo.
    pause
    exit /b 1
)

echo.
echo  HydraHive Tailscale Connect
echo  ───────────────────────────
echo.

:: Prüfe ob Tailscale installiert ist
if not exist %TAILSCALE% (
    echo  [FEHLER] Tailscale nicht gefunden!
    echo  Bitte installiere Tailscale von: https://tailscale.com/download/windows
    echo.
    pause
    exit /b 1
)

:: Erst abmelden (falls in anderem Tailnet)
echo  [1/3] Melde von altem Tailnet ab...
%TAILSCALE% logout 2>nul

:: Mit Auth Key verbinden
echo  [2/3] Verbinde mit HydraHive Tailnet...
%TAILSCALE% up --authkey=%~1 --reset

if errorlevel 1 (
    echo.
    echo  [FEHLER] Verbindung fehlgeschlagen!
    echo  Prüfe ob der Auth-Key korrekt ist und nicht abgelaufen.
    echo.
    pause
    exit /b 1
)

:: Status anzeigen
timeout /t 3 /nobreak >nul
echo  [3/3] Prüfe Verbindung...
echo.
%TAILSCALE% status

echo.
echo  ════════════════════════════════════════════════
echo.
echo   Verbunden! Du kannst jetzt auf den HydraHive
echo   Server zugreifen. Oeffne im Browser:
echo.
echo   https://[TAILSCALE-IP-DES-SERVERS]
echo.
echo  ════════════════════════════════════════════════
echo.
pause
