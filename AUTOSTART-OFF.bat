@echo off
REM ====================================================================
REM  Stops the clinic system starting by itself when you log in.
REM
REM  The system itself is untouched and no patient records are affected.
REM  You can still start it whenever you like with START-CLINIC.bat.
REM ====================================================================
title Clinic System - turn off automatic start
cd /d "%~dp0"

echo.
echo   ================================================
echo      Stop the clinic system starting automatically
echo   ================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$link = Join-Path ([Environment]::GetFolderPath('Startup')) 'Clinic System.lnk'; if (Test-Path $link) { Remove-Item $link -Force }"

echo   The clinic system will no longer start on its own.
echo   Double-click START-CLINIC.bat to start it when you need it.
echo.
echo   Nothing else has changed. No patient records were touched.
echo.
pause
