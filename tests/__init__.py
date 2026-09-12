"""Tests. `python3 -m tests` runs everything; `python3 -m tests recipes dashboard`
runs those groups.

One module per subject, and importing one runs its checks: a failure names
the area before it names the case. No framework and no dependencies beyond
the tool's own, so it runs anywhere the tool does.
"""

import importlib
import sys

# In the order they run. `online` is only run when asked for.
GROUPS = ("upstreams", "architectures", "recipes", "register", "payloads",
          "reading_payloads", "projects", "checking", "dashboard",
          "updater", "packing", "from_a_file", "database",
          "tracking", "dependencies", "imports")


def main(argv=None):
    from . import harness
    argv = sys.argv[1:] if argv is None else argv
    asked = [word for word in argv if not word.startswith("--")]
    unknown = [word for word in asked if word not in GROUPS]
    if unknown:
        print(f"no such test group: {', '.join(unknown)}\n"
              f"groups: {', '.join(GROUPS)}")
        return 2
    for name in asked or GROUPS:
        importlib.import_module(f"{__name__}.{name}")
    if "--online" in argv:
        importlib.import_module(f"{__name__}.online")
    for name, why in harness.FAILED:
        print(f"FAIL  {name}\n        {why}")
    for name in harness.PASSED:
        print(f"ok    {name}")
    print(f"\n{len(harness.PASSED)} passed, {len(harness.FAILED)} failed")
    return 1 if harness.FAILED else 0
