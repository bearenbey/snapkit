#!/usr/bin/env python3
"""`./tests.py` runs the tests; the suite itself is the `tests` package.

    ./tests.py                    everything that needs no network
    ./tests.py recipes dashboard  only those groups, by name
    ./tests.py --online           everything, and the ones that talk to GitHub
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests import main                                        # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
