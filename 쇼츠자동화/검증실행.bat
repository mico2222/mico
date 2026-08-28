@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 verify.py
    goto :end
)
where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python verify.py
    goto :end
)
echo Python not found. Please install Python 3.10 or later from https://python.org
pause
exit /b 1
:end
