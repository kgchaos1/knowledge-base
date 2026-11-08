@echo off
setlocal
chcp 65001 >nul
rem Quoted native Windows paths preserve spaces and Unicode directory names.
if not exist "%~dp0backup_client.py" (
  echo ERROR: backup_client.py is missing beside this launcher.
  pause
  exit /b 1
)
python -c "import sys, tkinter; assert sys.version_info >= (3, 9)" >nul 2>&1
if not errorlevel 1 (
  rem Keep the console so startup errors are visible instead of silently lost.
  python "%~dp0backup_client.py" --gui
  if errorlevel 1 goto failed
  exit /b 0
)
py -3 -c "import sys, tkinter; assert sys.version_info >= (3, 9)" >nul 2>&1
if not errorlevel 1 (
  py -3 "%~dp0backup_client.py" --gui
  if errorlevel 1 goto failed
  exit /b 0
)
echo ERROR: Python 3.9+ with tkinter was not found.
echo This launcher does not install software. Ask your administrator to provide Python with tkinter.
echo If Python is already installed, check PATH or the Windows Python launcher.
pause
exit /b 1
:failed
echo.
echo ERROR: The PKM backup GUI could not start or stopped unexpectedly. See details above.
pause
exit /b 1
