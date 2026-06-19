@echo off
REM ============================================================
REM Create isolated preprocessing environment for Flamingo
REM
REM Installs basicpy (flat-field correction) and leonardo-toolset
REM (dual-illumination fusion) in a separate Python venv to avoid
REM dependency conflicts with the main application:
REM   - basicpy hard-pins scipy<1.13 (app needs scipy>=1.14)
REM   - leonardo-toolset forces zarr<3 (app needs zarr>=3.1.4)
REM See claude-reports\2026-06-15-python-dependency-review.md.
REM
REM Location: %APPDATA%\Flamingo\preprocessing_env
REM Python:   requires 3.10+ (leonardo-toolset requirement)
REM
REM GPU vs CPU:
REM   Leonardo's deep-learning fusion is GPU-bound — CPU is impractical
REM   at scale. Default is GPU (CUDA) torch + jax. For a CPU-only box that
REM   only needs flat-field, set FLAMINGO_PREPROC_DEVICE=cpu.
REM   Match the CUDA wheel tag to your driver via FLAMINGO_PREPROC_CUDA
REM   (e.g. cu121, cu124, cu126).
REM
REM     set FLAMINGO_PREPROC_DEVICE=gpu   (default)
REM     set FLAMINGO_PREPROC_CUDA=cu124   (default)
REM     set FLAMINGO_PREPROC_DEVICE=cpu
REM
REM Size: ~6 GB GPU / ~3 GB CPU. Only needed once.
REM
REM Run this once. The stitching dialog can also trigger it via
REM the 'Setup Preprocessing...' button.
REM ============================================================

set ENV_DIR=%APPDATA%\Flamingo\preprocessing_env
if "%FLAMINGO_PREPROC_DEVICE%"=="" set FLAMINGO_PREPROC_DEVICE=gpu
if "%FLAMINGO_PREPROC_CUDA%"=="" set FLAMINGO_PREPROC_CUDA=cu124
set DEVICE=%FLAMINGO_PREPROC_DEVICE%
set CUDA_TAG=%FLAMINGO_PREPROC_CUDA%

REM Pinned to the versions validated in the dependency review (2026-06-15).
set BASICPY_SPEC=basicpy==2.0.0
set LEONARDO_SPEC=leonardo-toolset==1.1.1

echo.
echo ============================================================
echo  Flamingo Preprocessing Environment Setup
echo ============================================================
echo.
echo  Location: %ENV_DIR%
echo  Device:   %DEVICE% (CUDA %CUDA_TAG% if GPU)
echo  Installs: torch/jax (%DEVICE%), %BASICPY_SPEC%, %LEONARDO_SPEC%
echo  Download: ~6 GB (GPU) / ~3 GB (CPU). Only needed once.
echo.

REM Check if environment already exists
if exist "%ENV_DIR%\Scripts\python.exe" (
    echo Environment already exists at %ENV_DIR%
    echo To recreate, delete that folder and run this script again.
    echo.
    echo Checking installed packages...
    "%ENV_DIR%\Scripts\pip" list 2>nul | findstr /i "basicpy leonardo torch jax"
    echo.
    pause
    exit /b 0
)

REM Create parent directory
if not exist "%APPDATA%\Flamingo" mkdir "%APPDATA%\Flamingo"

echo [1/6] Creating virtual environment...
python -m venv "%ENV_DIR%"
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment.
    echo Make sure Python 3.10+ is installed and on PATH.
    pause
    exit /b 1
)

echo [2/6] Upgrading pip...
"%ENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo WARNING: pip upgrade failed, continuing with existing version...
)

if /i "%DEVICE%"=="cpu" (
    echo [3/6] Installing PyTorch ^(CPU^)...
    "%ENV_DIR%\Scripts\pip" install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    echo [4/6] Installing JAX ^(CPU^)...
    "%ENV_DIR%\Scripts\pip" install jax
) else (
    echo [3/6] Installing PyTorch ^(GPU, CUDA %CUDA_TAG%^)...
    "%ENV_DIR%\Scripts\pip" install torch torchvision --index-url https://download.pytorch.org/whl/%CUDA_TAG%
    echo [4/6] Installing JAX ^(GPU, CUDA 12^)...
    REM Installed before leonardo so its plain 'jax' dep is satisfied by the
    REM CUDA build (pip won't downgrade it to the CPU wheel).
    REM NOTE: torch and jax[cuda12] pin slightly different nvidia-cudnn-cu12
    REM builds, so pip prints a version-conflict warning here. Expected and
    REM harmless — both still initialise the GPU (validated on an RTX 3090).
    "%ENV_DIR%\Scripts\pip" install "jax[cuda12]"
)
if errorlevel 1 (
    echo ERROR: Failed to install torch/jax.
    pause
    exit /b 1
)

echo [5/6] Installing basicpy (flat-field correction)...
"%ENV_DIR%\Scripts\pip" install "%BASICPY_SPEC%" numpy
if errorlevel 1 (
    echo WARNING: basicpy installation failed.
    echo Flat-field correction will not be available.
    echo Continuing with remaining packages...
)

echo [6/6] Installing leonardo-toolset (dual-illumination fusion)...
"%ENV_DIR%\Scripts\pip" install "%LEONARDO_SPEC%"
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
"%ENV_DIR%\Scripts\pip" list 2>nul | findstr /i "basicpy leonardo torch jax numpy scipy"
echo.
if /i not "%DEVICE%"=="cpu" (
    echo GPU check:
    "%ENV_DIR%\Scripts\python.exe" -c "import torch; print(' torch CUDA available:', torch.cuda.is_available())"
    "%ENV_DIR%\Scripts\python.exe" -c "import jax; print(' jax devices:', jax.devices())"
    echo.
)
echo You can now enable flat-field correction and Leonardo FUSE
echo in the Tile Stitching dialog.
echo.
pause
