#!/bin/bash
# Script to update the Python environment with new 3D visualization dependencies

echo "================================================"
echo "Updating Flamingo Control Environment"
echo "================================================"
echo ""

# Check if we're in a virtual environment
if [[ "$VIRTUAL_ENV" != "" ]]; then
    echo "✓ Virtual environment detected: $VIRTUAL_ENV"
else
    echo "⚠ Warning: No virtual environment detected!"
    echo "  It's recommended to use a virtual environment."
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""
echo "Installing new dependencies for 3D visualization..."
echo ""

# Upgrade pip first
pip install --upgrade pip

# Install new requirements
pip install -r requirements.txt

# Check if napari was installed successfully
if python -c "import napari" 2>/dev/null; then
    echo ""
    echo "✓ napari installed successfully"
else
    echo ""
    echo "⚠ napari installation may have issues"
    echo "  Try: pip install napari[all]"
fi

# Check if sparse was installed successfully
if python -c "import sparse" 2>/dev/null; then
    echo "✓ sparse installed successfully"
else
    echo "⚠ sparse installation failed"
fi

echo ""
echo "================================================"
echo "Testing 3D visualization imports..."
echo "================================================"
echo ""

# Test the imports
python -c "
import sys
sys.path.insert(0, 'src')
try:
    from py2flamingo.visualization import DualResolutionVoxelStorage, CoordinateTransformer
    print('✓ Visualization modules import successfully')
except ImportError as e:
    print(f'✗ Error importing visualization modules: {e}')

try:
    from py2flamingo.views.sample_3d_visualization_window import Sample3DVisualizationWindow
    print('✓ 3D visualization window imports successfully')
except ImportError as e:
    print(f'✗ Error importing 3D window: {e}')

try:
    import napari
    print(f'✓ napari version: {napari.__version__}')
except ImportError:
    print('✗ napari not available')

try:
    import sparse
    print(f'✓ sparse available')
except ImportError:
    print('✗ sparse not available')

try:
    import yaml
    print('✓ PyYAML available')
except ImportError:
    print('✗ PyYAML not available')
"

echo ""
echo "================================================"
echo "Environment update complete!"
echo "================================================"
echo ""
echo "To test the 3D visualization:"
echo "  python test_3d_visualization.py"
echo ""
echo "Or from the main application:"
echo "  python -m py2flamingo"
echo "  Then: View → 3D Sample Visualization (Ctrl+3)"
echo ""