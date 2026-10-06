@echo off
setlocal
if "%~1"=="" (
  echo Drag a Markdown file onto this launcher.
  pause
  exit /b 1
)
call "%~dp0icviews.cmd" check "%~f1"
if errorlevel 1 goto failed
echo.
echo This will upload images and PUBLICLY publish: %~f1
choice /C YN /N /M "Publish now? [Y/N] "
if errorlevel 2 exit /b 0
call "%~dp0icviews.cmd" publish "%~f1" --yes
if errorlevel 1 goto failed
pause
exit /b 0
:failed
echo Stopped. Read the message above before retrying.
pause
exit /b 1
