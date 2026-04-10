@echo off
setlocal

cd /d "%~dp0.."

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 src\serve_woym.py
    goto :eof
)

where python >nul 2>nul
if %errorlevel%==0 (
    python src\serve_woym.py
    goto :eof
)

echo Python 3 was not found.
echo Install Python 3, then run this launcher again.
pause
