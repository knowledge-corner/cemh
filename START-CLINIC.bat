@echo off
REM ====================================================================
REM  Starts the clinic system. Double-click this file.
REM
REM  Written for someone who does not use a terminal, so it explains what
REM  is wrong rather than closing instantly on an error, and it waits for
REM  the system to actually answer before opening the browser.
REM ====================================================================
title Clinic System
cd /d "%~dp0"

echo.
echo   ================================================
echo      Centre for Endocrine ^& Metabolic Health
echo      Starting the clinic system
echo   ================================================
echo.

REM --- Is Docker installed? -------------------------------------------
where docker >nul 2>&1
if errorlevel 1 (
  echo   Docker is not installed on this computer.
  echo.
  echo   Install Docker Desktop from:
  echo       https://www.docker.com/products/docker-desktop/
  echo.
  echo   Then double-click this file again.
  echo.
  pause
  exit /b 1
)

REM --- Is Docker actually running? ------------------------------------
docker info >nul 2>&1
if errorlevel 1 (
  echo   Docker Desktop is installed but not running.
  echo.
  echo   1. Open Docker Desktop from the Start menu
  echo   2. Wait for the whale icon to stop moving
  echo   3. Double-click this file again
  echo.
  pause
  exit /b 1
)

echo   Starting... the first time takes a few minutes.
echo   Later starts take about ten seconds.
echo.

docker compose up -d --build
if errorlevel 1 (
  echo.
  echo   Something went wrong starting up.
  echo   Send this whole window to whoever supports the system.
  echo.
  pause
  exit /b 1
)

REM --- Wait until it actually answers before opening the browser ------
echo.
echo   Waiting for the clinic system to be ready...
set /a tries=0
:waitloop
set /a tries+=1
curl -s -o nul http://localhost:8000/ 2>nul
if not errorlevel 1 goto ready
if %tries% GEQ 60 goto slow
timeout /t 2 /nobreak >nul
goto waitloop

:slow
echo.
echo   It is taking longer than usual. Opening the browser anyway —
echo   if the page does not load, wait a minute and refresh.
goto open

:ready
echo   Ready.

:open
start "" http://localhost:8000/
echo.
echo   ================================================
echo      The clinic system is running.
echo.
echo      Address:   http://localhost:8000
echo.
echo      Reception: reception / clinicdemo2026
echo      Doctor:    vrushali / clinicdemo2026
echo.
echo      Leave it running. To shut it down, double-click
echo      STOP-CLINIC.bat
echo   ================================================
echo.
echo   You can close this window.
echo.
pause
