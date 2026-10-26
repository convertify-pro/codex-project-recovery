@echo off
setlocal DisableDelayedExpansion
title Codex Project Recovery
if not exist "%~dp0runtime\python.exe" goto missing
"%~dp0runtime\python.exe" -I "%~dp0one_click.py" %*
set "RECOVERY_EXIT=%ERRORLEVEL%"
echo.
echo Press any key to close this recovery window.
pause >nul
exit /b %RECOVERY_EXIT%
:missing
echo Please extract the ENTIRE ZIP first, then run START-RECOVERY.bat.
echo Keep the runtime folder next to this BAT file.
pause
exit /b 1
