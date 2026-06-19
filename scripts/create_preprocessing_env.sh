#!/usr/bin/env bash
# ============================================================
# Create isolated preprocessing environment for Flamingo
#
# Installs basicpy (flat-field correction) and leonardo-toolset
# (dual-illumination fusion) in a separate Python venv to avoid
# dependency conflicts with the main application:
#   - basicpy hard-pins scipy<1.13 (app needs scipy>=1.14)
#   - leonardo-toolset forces zarr<3 (app needs zarr>=3.1.4)
# So they cannot share the main env — see
# claude-reports/2026-06-15-python-dependency-review.md.
#
# Location: ~/.flamingo/preprocessing_env
# Python:   requires 3.10+ (leonardo-toolset requirement)
#
# GPU vs CPU:
#   Leonardo's deep-learning fusion is GPU-bound — CPU is impractical at scale.
#   Default is GPU (CUDA) torch + jax. On a CPU-only box that only needs
#   flat-field, set FLAMINGO_PREPROC_DEVICE=cpu.
#   Match the CUDA wheel tag to your driver with FLAMINGO_PREPROC_CUDA
#   (e.g. cu121, cu124, cu126).
#
#     FLAMINGO_PREPROC_DEVICE=gpu ./create_preprocessing_env.sh   # default
#     FLAMINGO_PREPROC_CUDA=cu124                                 # default
#     FLAMINGO_PREPROC_DEVICE=cpu ./create_preprocessing_env.sh
#
# Size: ~6 GB GPU (CUDA torch+jax+leonardo deps), ~3 GB CPU.
#
# Run this once. The stitching dialog can also trigger it via
# the 'Setup Preprocessing...' button.
# ============================================================

set -e

ENV_DIR="$HOME/.flamingo/preprocessing_env"
DEVICE="${FLAMINGO_PREPROC_DEVICE:-gpu}"
CUDA_TAG="${FLAMINGO_PREPROC_CUDA:-cu124}"

# Pinned to the versions validated in the dependency review (2026-06-15).
BASICPY_SPEC="basicpy==2.0.0"
LEONARDO_SPEC="leonardo-toolset==1.1.1"

echo ""
echo "============================================================"
echo " Flamingo Preprocessing Environment Setup"
echo "============================================================"
echo ""
echo " Location: $ENV_DIR"
if [ "$DEVICE" = "cpu" ]; then
    echo " Device:   CPU"
else
    echo " Device:   GPU (CUDA $CUDA_TAG)"
fi
echo " Installs: torch/jax ($DEVICE), $BASICPY_SPEC, $LEONARDO_SPEC"
echo " Download: ~6 GB (GPU) / ~3 GB (CPU). Only needed once."
echo ""

# Check if environment already exists
if [ -x "$ENV_DIR/bin/python" ]; then
    echo "Environment already exists at $ENV_DIR"
    echo "To recreate, delete that folder and run this script again."
    echo ""
    echo "Checking installed packages..."
    "$ENV_DIR/bin/pip" list 2>/dev/null | grep -iE "basicpy|leonardo|torch|jax"
    exit 0
fi

# Create parent directory
mkdir -p "$HOME/.flamingo"

echo "[1/6] Creating virtual environment..."
python3 -m venv "$ENV_DIR"

echo "[2/6] Upgrading pip..."
"$ENV_DIR/bin/python" -m pip install --upgrade pip || true

if [ "$DEVICE" = "cpu" ]; then
    echo "[3/6] Installing PyTorch (CPU)..."
    "$ENV_DIR/bin/pip" install torch torchvision \
        --index-url https://download.pytorch.org/whl/cpu

    echo "[4/6] Installing JAX (CPU)..."
    "$ENV_DIR/bin/pip" install "jax"
else
    echo "[3/6] Installing PyTorch (GPU, CUDA $CUDA_TAG)..."
    "$ENV_DIR/bin/pip" install torch torchvision \
        --index-url "https://download.pytorch.org/whl/${CUDA_TAG}"

    echo "[4/6] Installing JAX (GPU, CUDA 12)..."
    # Installed before leonardo so its plain 'jax' dependency is already
    # satisfied by the CUDA build (pip won't downgrade it to the CPU wheel).
    # NOTE: torch and jax[cuda12] pin slightly different nvidia-cudnn-cu12
    # builds, so pip prints a version-conflict warning here. This is expected
    # and harmless — both still initialise the GPU (validated on an RTX 3090:
    # torch.cuda.is_available()=True and jax.devices()=[CudaDevice]).
    "$ENV_DIR/bin/pip" install "jax[cuda12]"
fi

echo "[5/6] Installing basicpy (flat-field correction)..."
"$ENV_DIR/bin/pip" install "$BASICPY_SPEC" numpy || {
    echo "WARNING: basicpy installation failed."
    echo "Flat-field correction will not be available."
}

echo "[6/6] Installing leonardo-toolset (dual-illumination fusion)..."
"$ENV_DIR/bin/pip" install "$LEONARDO_SPEC" || {
    echo "WARNING: leonardo-toolset installation failed."
    echo "Leonardo FUSE will not be available."
}

echo ""
echo "============================================================"
echo " Setup complete!"
echo "============================================================"
echo ""
echo "Installed packages:"
"$ENV_DIR/bin/pip" list 2>/dev/null | grep -iE "basicpy|leonardo|torch|jax|numpy|scipy"
echo ""
if [ "$DEVICE" != "cpu" ]; then
    echo "GPU check:"
    "$ENV_DIR/bin/python" -c "import torch; print(' torch CUDA available:', torch.cuda.is_available())" 2>/dev/null || true
    "$ENV_DIR/bin/python" -c "import jax; print(' jax devices:', jax.devices())" 2>/dev/null || true
    echo ""
fi
echo "You can now enable flat-field correction and Leonardo FUSE"
echo "in the Tile Stitching dialog."
