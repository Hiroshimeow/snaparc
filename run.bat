@echo off
chcp 65001 >nul 2>nul
setlocal
set "PATH=%SystemRoot%\System32;%SystemRoot%\SysWOW64;%SystemRoot%;%PATH%"
pushd "%~dp0" && (
  echo PATH fixed ^(System32 first^). Running...
  "C:\Users\admin\miniconda3\Scripts\conda.exe" run -n ana11 --no-capture-output --live-stream python main.py %*
) & popd
endlocal
pause
