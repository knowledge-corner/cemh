@echo off
REM ====================================================================
REM  Moves one patient's whole record from this computer's local copy of
REM  the clinic system to the live server. Double-click this file.
REM
REM  Does three things in order, so nobody has to remember the steps or
REM  type a command by hand:
REM    1. Exports the patient from the local database to a file.
REM    2. Sends that file to the server over the same secure connection
REM       used to manage the server — never through GitHub or email,
REM       since this file holds a real patient's medical record.
REM    3. Tells the server to read the file in and stops there — nothing
REM       else needs doing on the server afterwards.
REM
REM  Refuses to overwrite an existing patient there: if someone with the
REM  same phone number, or the same clinic ID, already exists on the
REM  server, this stops and says so rather than guessing.
REM ====================================================================
title Move a patient to the live clinic system
cd /d "%~dp0"

REM --- Settings — change these if the server ever moves ---------------
set DROPLET_IP=64.227.145.188
set DROPLET_SSH_KEY=%USERPROFILE%\.ssh\id_ed25519
set REMOTE_APP_DIR=/opt/cemh

echo.
echo   ================================================
echo      Move a patient to the live clinic system
echo   ================================================
echo.

set /p PATIENT_UHID="Enter the patient's clinic ID (e.g. CEMH-26-00003): "
if "%PATIENT_UHID%"=="" (
  echo.
  echo   Nothing entered - stopping.
  echo.
  pause
  exit /b 1
)

set EXPORT_FILE=patient_%PATIENT_UHID%_export.json

echo.
echo   [1/3] Exporting %PATIENT_UHID% from this computer's local copy...
docker compose exec -T web python manage.py export_patient %PATIENT_UHID% --out /app/%EXPORT_FILE%
if errorlevel 1 (
  echo.
  echo   Could not export that patient. Check the clinic ID is correct and
  echo   that the local clinic system is running, then try again.
  echo.
  pause
  exit /b 1
)

if not exist "%EXPORT_FILE%" (
  echo.
  echo   The export command finished but the file was not found here. Is
  echo   the local clinic system running with "docker compose up" from
  echo   this same folder? Nothing was sent to the server.
  echo.
  pause
  exit /b 1
)

echo.
echo   [2/3] Sending the file to the server...
scp -i "%DROPLET_SSH_KEY%" "%EXPORT_FILE%" root@%DROPLET_IP%:%REMOTE_APP_DIR%/
if errorlevel 1 (
  echo.
  echo   Could not reach the server. Check your internet connection, then
  echo   try again - nothing on the server has changed.
  echo.
  pause
  exit /b 1
)

echo.
echo   [3/3] Reading the patient into the live clinic database...
ssh -i "%DROPLET_SSH_KEY%" root@%DROPLET_IP% "cd %REMOTE_APP_DIR% && docker compose -f docker-compose.prod.yml exec -T web python manage.py import_patient %EXPORT_FILE%"
if errorlevel 1 (
  echo.
  echo   ================================================================
  echo     The server refused this import - read what it said above.
  echo     Nothing was changed on the live database: it is all-or-nothing,
  echo     so a refusal here means the patient was NOT added twice.
  echo   ================================================================
  echo.
  pause
  exit /b 1
)

echo.
echo   ================================================================
echo     Done. %PATIENT_UHID% is now on the live clinic system.
echo   ================================================================
echo.
pause
