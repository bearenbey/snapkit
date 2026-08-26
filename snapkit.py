#!/usr/bin/env python3
"""snapkit -- make a snap out of a GitHub repository or a file you have.

    snapkit                        the dashboard: create, check, update, build
    snapkit create owner/name      make a snap from a repository
    snapkit create ./thing.deb     ... or from a .deb, archive or AppImage
    snapkit list                   what is registered
    snapkit check                  what has a newer release upstream
    snapkit update <name>          move a snap onto that release and rebuild
    snapkit remove <name>          forget a snap, and its recipe with it

Give it a repository and it resolves the newest release, picks the file that
can actually be packaged out of everything attached to it, downloads that
file, opens it to find out where the program and its desktop entry and icon
really are, writes a snapcraft.yaml around what it found, and builds it.

Not everything is published as a release, so not everything has to be given
as one: point it at a file instead and that file is what gets packaged, and
the snap is then kept in step with the folder it sits in.

Every snap made this way is registered in one JSON file, which holds the
recipe as well as the record -- so a project directory can be thrown away and
written out again, an update is one keystroke, and removing a snap removes
the whole of it.

Release metadata is read from pages that are not rate limited (the
/releases/latest redirect, the releases atom feed, expanded_assets) rather
than api.github.com, which returns 403 from a shared address often enough to
matter on the twentieth repository of the afternoon.
"""

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
