@echo off
setlocal
cd /d "%~dp0"
title CSP File Manager - TEST ONLY
set "PYTHONUNBUFFERED=1"
set "CSP_PORT=8010"

set "PYTHON=D:\Python3.12.0\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo ============================================================
echo  CSP File Manager - TEST / PRACTICE ONLY
echo  URL: http://127.0.0.1:8010
echo  Logs are shown below in real time.
echo  Close this window to stop the server.
echo ============================================================
echo.

"%PYTHON%" app.py

echo.
echo Server stopped.
pause
