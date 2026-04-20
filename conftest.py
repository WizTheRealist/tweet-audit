import sys
from pathlib import Path

# Allow test files to import modules from src/ without installation
sys.path.insert(0, str(Path(__file__).parent / "src"))