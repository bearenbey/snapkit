#!/usr/bin/env python3
"""Build the snapkit snap."""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from snapforge.build import BuildError, snapcraft_preflight      # noqa: E402
from snapforge.report import PlainReporter                        # noqa: E402


def die(text):
    print(f"error: {text}", file=sys.stderr)
    raise SystemExit(1)


def main():
    reporter = PlainReporter()
    flags = sys.argv[1:]
    try:
        # The same checks a project's build gets, before the minutes start.
        snapcraft_preflight("--destructive-mode" in flags, reporter)
    except BuildError as exc:
        die(str(exc))

    reporter.step("snapcraft pack")
    done = subprocess.run(["snapcraft", "pack", *flags], cwd=HERE)
    if done.returncode != 0:
        die(f"snapcraft exited with status {done.returncode}")

    built = sorted(HERE.glob("snapkit_*.snap"), key=lambda p: p.stat().st_mtime)
    if not built:
        die("snapcraft finished but produced no .snap")
    reporter.result(f"{built[-1].name}  ({built[-1].stat().st_size / 1e6:.0f} MB)")
    reporter.detail(f"install it with: sudo snap install --dangerous --classic "
                    f"{built[-1].name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        raise SystemExit(130) from None
