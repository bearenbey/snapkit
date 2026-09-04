"""Replacing a version wherever it is spelled out, and saying what changed."""

import re
from dataclasses import dataclass
from pathlib import Path

# Where a version can be spelled out, including scripts that only might.
TEXT_FILES = ("README.md", "build.py", "diagnose.py", "update-version.py",
              "snap/snapcraft.yaml", "overlay/meta/snap.yaml")


@dataclass
class FileChange:
    path: str
    lines: list          # [(lineno, new text)]


def replace_version(text, old, new):
    """Swap one version for another, only where it is the whole version."""
    if not old:
        return text
    return re.sub(rf"(?<![0-9A-Za-z])([vV]?){re.escape(old)}(?![0-9])(?!\.[0-9])",
                  lambda found: found.group(1) + new, text)


def _write_lines(path, lines):
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _changed(before, after):
    return [(n + 1, b) for n, (a, b) in enumerate(zip(before, after)) if a != b]


def rewrite_versions(directory, old, new, old_asset="", new_asset=""):
    """Replace every mention of the old version and artifact name."""
    changes = []
    if not old or old == new:
        old = ""       # nothing to swap, but an asset rename may still apply

    for name in TEXT_FILES:
        path = Path(directory) / name
        if not path.is_file():
            continue
        before = path.read_text(encoding="utf-8", errors="replace").splitlines()
        after = list(before)

        if old_asset and old_asset != new_asset:
            after = [line.replace(old_asset, new_asset) for line in after]
        if old:
            after = [replace_version(
                        replace_version(line, old, new),
                        old.replace("-", "_"), new.replace("-", "_"))
                     for line in after]

        lines = _changed(before, after)
        if lines:
            _write_lines(path, after)
            changes.append(FileChange(name, lines))
    return changes


def repoint_yaml(path, anchor, url, sha, version=""):
    """Point a snapcraft part at a new source tarball and checksum."""
    path = Path(path)
    before = path.read_text(encoding="utf-8", errors="replace").splitlines()
    after = []
    for line in before:
        found = re.match(anchor, line)
        if found:
            prefix = found.group(1) if found.re.groups else found.group(0)
            line = f"{prefix}{url}"
        elif re.match(r"^\s*source-checksum:\s*sha256/", line):
            line = re.sub(r"(sha256/).*", lambda m: m.group(1) + sha, line)
        elif version and re.match(r"^version:", line):
            line = f"version: '{version}'"
        after.append(line)

    lines = _changed(before, after)
    if not lines:
        return []
    _write_lines(path, after)
    return [FileChange(f"{path.parent.name}/{path.name}", lines)]
