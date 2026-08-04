@echo off
REM ====================================================================
REM  Makes the clinic system start by itself when you log in.
REM
REM  Double-click this once. From then on, switching the computer on
REM  starts the system and opens it in your browser, with nothing to
REM  click. Double-click AUTOSTART-OFF.bat to stop that happening.
REM ====================================================================
title Clinic System - turn on automatic start
cd /d "%~dp0"

echo.
echo   ================================================
echo      Start the clinic system automatically
echo   ================================================
echo.

REM  A shortcut in the Startup folder is how Windows does this. It is created
REM  here rather than explained in a document, because dragging a shortcut into
REM  a hidden folder is the step people get wrong.
REM
REM  /auto tells START-CLINIC.bat that nobody is sitting there to press a key
REM  when it finishes. WindowStyle 7 is minimised, so logging in is not
REM  interrupted by a black window. It is still in the taskbar if you want it.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$link = Join-Path ([Environment]::GetFolderPath('Startup')) 'Clinic System.lnk'; $s = (New-Object -ComObject WScript.Shell).CreateShortcut($link); $s.TargetPath = '%~dp0START-CLINIC.bat'; $s.Arguments = '/auto'; $s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7; $s.Description = 'Starts the clinic system and opens it in the browser'; $s.Save()"

if errorlevel 1 (
  echo   Could not set it up.
  echo   Send this whole window to whoever supports the system.
  echo.
  pause
  exit /b 1
)

echo   Done.
echo.
echo   The clinic system will now start on its own when you log in,
echo   and open at http://localhost:8000
echo.
echo   One more thing, if it is not already set:
echo     open Docker Desktop, go to Settings, and tick
echo     "Start Docker Desktop when you log in".
echo   Without that there is nothing for this to start.
echo.
echo   To undo this, double-click AUTOSTART-OFF.bat
echo.
pause
