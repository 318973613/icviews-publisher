@echo off
setlocal
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  call "%~dp0setup.cmd"
  if errorlevel 1 exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0entry.py" %*
