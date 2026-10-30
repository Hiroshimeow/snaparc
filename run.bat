@echo off
chcp 65001 >nul 2>nul
setlocal
call "%~dp0main.bat" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo SnapArc exited with code %RC%.
pause
exit /b %RC%
