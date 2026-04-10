@echo off
setlocal

cd /d "%~dp0.."
set "SCRIPT=src\serve_woym.py"

where py >nul 2>nul
if %errorlevel%==0 (
    echo Starting WOYM monitor with py -3...
    py -3 "%SCRIPT%"
    if %errorlevel%==0 goto :eof
    echo.
    echo py -3 failed with exit code %errorlevel%.
)

where python >nul 2>nul
if %errorlevel%==0 (
    echo Starting WOYM monitor with python...
    python "%SCRIPT%"
    if %errorlevel%==0 goto :eof
    echo.
    echo python failed with exit code %errorlevel%.
)

echo.
echo Could not start the WOYM monitor.
echo Check the error shown above. If no Python command was found, install Python 3 and run this launcher again.
pause
