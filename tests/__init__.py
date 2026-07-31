"""Put src/ on sys.path so the tests run from a fresh clone.

Without this, `python -m unittest discover -s tests` fails with
ModuleNotFoundError: No module named 'kmmx' unless the package has already
been installed. Installing (`pip install .`) still works and is what CI does;
this just removes the surprise for someone who clones and runs the tests
straight away.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
