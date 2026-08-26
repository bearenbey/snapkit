#!/usr/bin/env python3
"""Build the snapkit snap.

    ./build.py                  snapcraft pack
    ./build.py --destructive-mode    ... on this host, without a container

Anything on the command line is passed through to snapcraft. This project is
an ordinary snapcraft recipe, so this script is a convenience rather than a
necessity -- what it adds is the check that snapcraft and a backend are there
before a build that takes minutes rather than after.
"""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def say(text):
    arrow = "\033[36m==>\033[0m" if sys.stdout.isatty() else "==>"
    print(f"{arrow} {text}", flush=True)


def die(text):
    print(f"error: {text}", file=sys.stderr)
    raise SystemExit(1)


def main():
    if not shutil.which("snapcraft"):
        die("snapcraft is not installed: sudo snap install snapcraft --classic")

    # snapcraft says this too, but only after pulling the recipe apart.
    lxd = shutil.which("lxc") and subprocess.run(
        ["lxc", "list"], capture_output=True).returncode == 0
    if not lxd and "--destructive-mode" not in sys.argv[1:]:
        print("warning: LXD is not answering, and it is snapcraft's default "
              "backend:\n"
              "           sudo snap install lxd\n"
              "           sudo lxd init --auto\n"
              '           sudo usermod -aG lxd "$USER"   # then: newgrp lxd',
              file=sys.stderr)

    say("snapcraft pack")
    done = subprocess.run(["snapcraft", "pack", *sys.argv[1:]], cwd=HERE)
    if done.returncode != 0:
        die(f"snapcraft exited with status {done.returncode}")

    built = sorted(HERE.glob("snapkit_*.snap"), key=lambda p: p.stat().st_mtime)
    if not built:
        die("snapcraft finished but produced no .snap")
    say(f"{built[-1].name}  ({built[-1].stat().st_size / 1e6:.0f} MB)")
    say(f"install it with: sudo snap install --dangerous --classic {built[-1].name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        raise SystemExit(130)
