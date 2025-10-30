import sys
from pathlib import Path

# Ensure the src directory is on the Python path for imports
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
