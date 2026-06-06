import sys
from pathlib import Path

# Ensure project root is in sys.path when running evals standalone
sys.path.insert(0, str(Path(__file__).parent.parent))
