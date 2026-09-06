#!/usr/bin/env python3
"""snapkit -- make a snap out of a GitHub repository or a file you have."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from snapforge.cli import entry

if __name__ == "__main__":
    entry()
