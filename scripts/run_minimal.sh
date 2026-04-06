#!/bin/bash
# Launch minimal Flamingo control interface

# Activate virtual environment
source .venv/bin/activate

# Set PYTHONPATH to include src directory
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"

# Run the minimal GUI
cd src
python -m py2flamingo.minimal_gui
