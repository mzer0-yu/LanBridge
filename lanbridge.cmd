@echo off
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Please run install.ps1 first. 1>&2
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -B "%~dp0run.py" %*
exit /b %errorlevel%
