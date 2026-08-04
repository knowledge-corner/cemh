@echo off
REM  Shuts the clinic system down. Double-click this file.
REM  Patient records are kept - they live in a Docker volume, not in the
REM  containers this stops.
title Clinic System - Shutting down
cd /d "%~dp0"

echo.
echo   Shutting down the clinic system...
echo.

docker compose down

echo.
echo   Stopped. All patient records have been kept.
echo   Double-click START-CLINIC.bat when you need it again.
echo.
pause
