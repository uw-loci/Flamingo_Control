@echo off
REM ============================================================
REM Create isolated preprocessing environment for Flamingo
REM
REM Installs basicpy (flat-field correction) and leonardo-toolset
REM (dual-illumination fusion) in a separate Python venv to avoid
REM dependency conflicts with the main application.
REM
REM Location: %APPDATA%\Flamingo\preprocessing_env
REM Size: ~3 GB (torch-cpu, basicpy, leonardo-toolset + deps)
REM
REM Run this once. The stitching dialog can also trigger it via
REM the 'Setup Preprocessing...' button.
REM ============================================================

set ENV_DIR=%APPDATA%\Flamingo\preprocessing_env

echo.
echo ============================================================
echo  Flamingo Preprocessing Environment Setup
echo ============================================================
echo.
echo  Location: %ENV_DIR%
echo  This will install torch (CPU), basicpy, and leonardo-toolset.
echo  Download size: ~3 GB. Only needed once.
echo.

REM Check if environment already exists
if exist "%ENV_DIR%\Scripts\python.exe" (
    echo Environment already exists at %ENV_DIR%
    echo To recreate, delete that folder and run this script again.
    echo.
    echo Checking installed packages...
    "%ENV_DIR%\Scripts\pip" list 2>nul | findstr /i "basicpy leonardo torch"
    echo.
    pause
    exit /b 0
)

REM Create parent directory
if not exist "%APPDATA%\Flamingo" mkdir "%APPDATA%\Flamingo"

echo [1/5] Creating virtual environment...
python -m venv "%ENV_DIR%"
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment.
    echo Make sure Python is installed and on PATH.
    pause
    exit /b 1
)

echo [2/5] Upgrading pip...
"%ENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo WARNING: pip upgrade failed, continuing with existing version...
)

echo [3/5] Installing PyTorch (CPU-only, ~200 MB)...
"%ENV_DIR%\Scripts\pip" install torch --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 (
    echo ERROR: Failed to install torch.
    pause
    exit /b 1
)

echo [4/5] Installing basicpy (flat-field correction)...
"%ENV_DIR%\Scripts\pip" install "basicpy>=2.0.0" numpy
if errorlevel 1 (
    echo WARNING: basicpy installation failed.
    echo Flat-field correction will not be available.
    echo Continuing with remaining packages...
)

echo [5/5] Installing leonardo-toolset (dual-illumination fusion)...
"%ENV_DIR%\Scripts\pip" install leonardo-toolset
if errorlevel 1 (
    echo WARNING: leonardo-toolset installation failed.
    echo Leonardo FUSE will not be available.
    echo This is OK — max/mean fusion will still work.
)

echo.
echo ============================================================
echo  Setup complete!
echo ============================================================
echo.
echo Installed packages:
"%ENV_DIR%\Scripts\pip" list 2>nul | findstr /i "basicpy leonardo torch numpy scipy"
echo.
echo You can now enable flat-field correction and Leonardo FUSE
echo in the Tile Stitching dialog.
echo.
pause
