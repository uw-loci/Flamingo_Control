@echo off
REM ============================================================
REM Flamingo Control - Update and Run
REM ============================================================
REM Pulls latest code, installs/upgrades dependencies, launches app.
REM Place on Desktop or run from any directory.
REM ============================================================

cd /d G:\Github\MichaelSNelson\Flamingo_Control

echo Pulling latest changes...
git pull
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: git pull failed
    pause
    exit /b 1
)

echo.
echo Installing Python packages...
.venv\Scripts\pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: pip install failed
    pause
    exit /b 1
)

REM Install packages that cause pip resolver backtracking when mixed
REM with the main requirements file. These are installed separately
REM with --no-deps to skip dependency resolution entirely.
.venv\Scripts\pip install pystripe==1.3.1 --no-deps 2>nul

echo.
echo === Update complete ===
echo.
echo Starting Flamingo Control...
.venv\Scripts\python -m py2flamingo
