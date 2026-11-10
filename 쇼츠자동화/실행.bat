@echo off
cd /d "%~dp0"

py -3.13 --version >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3.13 run.py
    goto :end
)

py -3 --version >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 run.py
    goto :end
)

python --version >nul 2>nul
if %ERRORLEVEL%==0 (
    python run.py
    goto :end
)

echo Python not found, or the "python" command on this PC does not work.
echo Please install Python 3.10 or later from https://python.org
echo IMPORTANT: on the install screen, check "Add python.exe to PATH".

:end
pause
