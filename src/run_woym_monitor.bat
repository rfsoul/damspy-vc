@echo off
setlocal

cd /d "%~dp0.."
set "SCRIPT=src\serve_woym.py"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3.11 -c "import PIL" >nul 2>nul
    if %errorlevel%==0 (
        echo Starting WOYM monitor with py -3.11...
        py -3.11 "%SCRIPT%"
        if %errorlevel%==0 goto :eof
        echo.
        echo py -3.11 failed with exit code %errorlevel%.
    ) else (
        echo Skipping py -3.11 because Pillow is not available in that interpreter.
    )
)

where python3.11 >nul 2>nul
if %errorlevel%==0 (
    python3.11 -c "import PIL" >nul 2>nul
    if %errorlevel%==0 (
        echo Starting WOYM monitor with python3.11...
        python3.11 "%SCRIPT%"
        if %errorlevel%==0 goto :eof
        echo.
        echo python3.11 failed with exit code %errorlevel%.
    ) else (
        echo Skipping python3.11 because Pillow is not available in that interpreter.
    )
)

echo.
echo Could not start the WOYM monitor with Python 3.11 and Pillow installed.
echo Install Pillow into Python 3.11, or make Python 3.11 available on PATH or via the py launcher.
pause
