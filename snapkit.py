#!/usr/bin/env python3
"""snapkit -- make a snap out of a GitHub repository or a file you have."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from snapforge.cli import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        raise SystemExit(130)
