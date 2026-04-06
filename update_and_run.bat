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
echo Installing/updating Python packages...
REM No --upgrade flag: only installs missing packages, doesn't
REM re-resolve the entire dependency tree (avoids pip backtracking).
.venv\Scripts\pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: pip install failed
    pause
    exit /b 1
)

echo.
echo === Update complete ===
echo.
echo Starting Flamingo Control...
.venv\Scripts\python -m py2flamingo
