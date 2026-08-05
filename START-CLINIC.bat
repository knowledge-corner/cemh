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

REM --- Get the latest version of the system ---------------------------
REM  So that starting Docker and double-clicking this file is the whole
REM  job: no separate "git pull" step to remember, and no chance of
REM  running last month's code against this month's database.
REM
REM  Three environment variables make git fail fast instead of hanging.
REM  Nobody is watching this window at 8am, and a git that sits waiting
REM  for a password or a dead network never opens the clinic at all.
set GIT_TERMINAL_PROMPT=0
set GIT_HTTP_LOW_SPEED_LIMIT=1000
set GIT_HTTP_LOW_SPEED_TIME=20

if defined CLINIC_UPDATED goto afterupdate
where git >nul 2>&1
if errorlevel 1 goto afterupdate
if not exist ".git" goto afterupdate

echo   Checking for updates...
set "BEFORE="
set "AFTER="
for /f "delims=" %%i in ('git rev-parse HEAD 2^>nul') do set "BEFORE=%%i"

REM  --ff-only, never a merge. This computer only ever receives changes,
REM  so a pull that cannot simply move forward is a situation for a human,
REM  not something to resolve automatically at half past eight in the
REM  morning. It refuses, says so, and the clinic still opens on the
REM  version already here.
git pull --ff-only
if errorlevel 1 (
  echo.
  echo   Could not fetch updates - carrying on with the version already
  echo   on this computer. The clinic system will still work. If this
  echo   keeps happening, send this window to whoever supports it.
  echo.
)
for /f "delims=" %%i in ('git rev-parse HEAD 2^>nul') do set "AFTER=%%i"

REM  If the update changed this very file, cmd.exe is now reading a batch
REM  script that has moved underneath it - it keeps its place by byte
REM  offset, so every line after this point could be read from the wrong
REM  spot and executed as nonsense. Starting again in a fresh window is
REM  the only reliable fix. CLINIC_UPDATED is inherited by that new
REM  process, so it pulls once and does not loop.
if not "%BEFORE%"=="%AFTER%" (
  echo.
  echo   Updated. Starting again with the new version...
  set CLINIC_UPDATED=1
  start "" "%~f0" %*
  exit /b 0
)
:afterupdate

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
REM  Straight after the computer is switched on, Docker Desktop needs half a
REM  minute or so before it will answer. Giving up immediately made this file
REM  useless at startup, which is exactly when it is most wanted - so it waits,
REM  and only complains if Docker really is not coming.
docker info >nul 2>&1
if not errorlevel 1 goto dockerready

echo   Waiting for Docker Desktop to be ready...
set /a dtries=0
:dockerwait
set /a dtries+=1
timeout /t 3 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto dockerready
if %dtries% LSS 40 goto dockerwait

echo.
echo   Docker Desktop did not become ready after two minutes.
echo.
echo   1. Open Docker Desktop from the Start menu
echo   2. Wait for the whale icon to stop moving
echo   3. Double-click this file again
echo.
pause
exit /b 1

:dockerready

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

REM --- Bring the database up to date with the code --------------------
REM  The container applies migrations when it starts, but the web server
REM  reloads changed code without restarting the container. So after a
REM  git pull the new code is live while the database is still on the old
REM  shape, and pages die with "column ... does not exist". Applying them
REM  here means pulling and double-clicking is always enough. It does
REM  nothing when there is nothing to apply.
echo.
echo   Checking the database is up to date...
set /a mtries=0
:migrate
set /a mtries+=1
docker compose exec -T web python manage.py migrate --no-input >nul 2>&1
if not errorlevel 1 goto migrated
if %mtries% LSS 5 (
  timeout /t 3 /nobreak >nul
  goto migrate
)
echo   Could not update the database. The system may still work; if a page
echo   shows an error about a missing column, send this window on.
:migrated

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
echo   It is taking longer than usual. Opening the browser anyway -
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

REM  Run from the Startup folder (AUTOSTART-ON.bat passes /auto) there is
REM  nobody sitting there to press a key.
if /i "%~1"=="/auto" exit /b 0
pause
