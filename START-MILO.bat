@echo off
setlocal
title MILO Desktop
cd /d "%~dp0"
if exist "%~dp0dist\Milo\Milo.exe" (
    start "" "%~dp0dist\Milo\Milo.exe"
    exit /b 0
)
if exist "%~dp0Milo.exe" (
    start "" "%~dp0Milo.exe"
    exit /b 0
)
py -3 -c "import tkinter; import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :python
py -3 -m desktop.milo_desktop
if errorlevel 1 goto :fail
exit /b 0
:python
echo A local Python 3.10 or newer with Tkinter is needed for the source version.
echo Download the prebuilt Milo.exe from a successful Windows GitHub Actions run.
goto :fail
:fail
echo.
echo Milo could not start. Check the output above.
pause
exit /b 1
