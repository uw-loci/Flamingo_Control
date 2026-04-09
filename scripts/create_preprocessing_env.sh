#!/usr/bin/env bash
# ============================================================
# Create isolated preprocessing environment for Flamingo
#
# Installs basicpy (flat-field correction) and leonardo-toolset
# (dual-illumination fusion) in a separate Python venv to avoid
# dependency conflicts with the main application.
#
# Location: ~/.flamingo/preprocessing_env
# Size: ~3 GB (torch-cpu, basicpy, leonardo-toolset + deps)
#
# Run this once. The stitching dialog can also trigger it via
# the 'Setup Preprocessing...' button.
# ============================================================

set -e

ENV_DIR="$HOME/.flamingo/preprocessing_env"

echo ""
echo "============================================================"
echo " Flamingo Preprocessing Environment Setup"
echo "============================================================"
echo ""
echo " Location: $ENV_DIR"
echo " This will install torch (CPU), basicpy, and leonardo-toolset."
echo " Download size: ~3 GB. Only needed once."
echo ""

# Check if environment already exists
if [ -x "$ENV_DIR/bin/python" ]; then
    echo "Environment already exists at $ENV_DIR"
    echo "To recreate, delete that folder and run this script again."
    echo ""
    echo "Checking installed packages..."
    "$ENV_DIR/bin/pip" list 2>/dev/null | grep -iE "basicpy|leonardo|torch"
    exit 0
fi

# Create parent directory
mkdir -p "$HOME/.flamingo"

echo "[1/5] Creating virtual environment..."
python3 -m venv "$ENV_DIR"

echo "[2/5] Upgrading pip..."
"$ENV_DIR/bin/python" -m pip install --upgrade pip || true

echo "[3/5] Installing PyTorch (CPU-only, ~200 MB)..."
"$ENV_DIR/bin/pip" install torch --index-url https://download.pytorch.org/whl/cpu

echo "[4/5] Installing basicpy (flat-field correction)..."
"$ENV_DIR/bin/pip" install "basicpy>=2.0.0" numpy || {
    echo "WARNING: basicpy installation failed."
    echo "Flat-field correction will not be available."
}

echo "[5/5] Installing leonardo-toolset (dual-illumination fusion)..."
"$ENV_DIR/bin/pip" install leonardo-toolset || {
    echo "WARNING: leonardo-toolset installation failed."
    echo "Leonardo FUSE will not be available."
}

echo ""
echo "============================================================"
echo " Setup complete!"
echo "============================================================"
echo ""
echo "Installed packages:"
"$ENV_DIR/bin/pip" list 2>/dev/null | grep -iE "basicpy|leonardo|torch|numpy|scipy"
echo ""
echo "You can now enable flat-field correction and Leonardo FUSE"
echo "in the Tile Stitching dialog."
