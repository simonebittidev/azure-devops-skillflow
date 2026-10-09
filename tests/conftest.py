import sys
from pathlib import Path

# Make the task's `src` package importable, as main.py does at runtime.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "task"))
