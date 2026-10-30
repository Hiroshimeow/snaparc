@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>&1
if errorlevel 1 (
  echo SnapArc requires uv: https://docs.astral.sh/uv/
  exit /b 1
)
uv run --locked --python 3.12 python main.py %*
exit /b %ERRORLEVEL%
