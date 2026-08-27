@echo off
setlocal

cd /d "%~dp0"
set "PORT=8765"
set "URL=http://127.0.0.1:%PORT%/"
set "DATA_DIR=%~dp0..\..\..\data"

where python >nul 2>&1
if not errorlevel 1 goto start_with_python

where py >nul 2>&1
if not errorlevel 1 goto start_with_py

echo Python was not found. Install Python 3 and enable the python or py command.
pause
exit /b 1

:start_with_python
start "Akito Story Import" /D "%~dp0" python web.py --data-dir "%DATA_DIR%" --host 127.0.0.1 --port %PORT%
goto open_browser

:start_with_py
start "Akito Story Import" /D "%~dp0" py -3 web.py --data-dir "%DATA_DIR%" --host 127.0.0.1 --port %PORT%

:open_browser
timeout /t 2 /nobreak >nul
start "" "%URL%"
echo Story import tool started: %URL%
echo The service runs in a separate window. Close it to stop the service.
exit /b 0
