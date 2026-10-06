@echo off
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Run setup.cmd first.
  exit /b 1
)
"%PY%" "%~dp0publish.py" %*
exit /b %errorlevel%
