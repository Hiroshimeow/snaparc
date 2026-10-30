@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0main.ps1" %*
exit /b %ERRORLEVEL%
