"""Replacing a version wherever it is spelled out, and saying what changed."""

import functools
import re
from dataclasses import dataclass
from pathlib import Path

# Where a version can be spelled out, including scripts that only might.
TEXT_FILES = ("README.md", "pack.py", "diagnose.py",
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


def _read_lines(path):
    """Lines split on newlines only, so \\r and a form feed stay in them."""
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        return handle.read().split("\n")


def _write_lines(path, lines):
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(lines))


def _outside(line, keep, swap):
    """`swap` applied to the parts of a line that are not `keep`."""
    if not keep or keep not in line:
        return swap(line)
    return keep.join(swap(part) for part in line.split(keep))


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
        before = _read_lines(path)
        after = list(before)

        if old_asset and old_asset != new_asset:
            after = [line.replace(old_asset, new_asset) for line in after]
        # One pass per spelling: a second pass over the same text would
        # find the new version inside itself when it starts with the old.
        spellings = [(old, new)] if old else []
        if old and "-" in old:
            spellings.append((old.replace("-", "_"), new.replace("-", "_")))
        for was, now in spellings:
            # Not inside the asset's new name, which already carries the
            # new version and may start with the old: 2.0 in 2.0-1.
            swap = functools.partial(replace_version, old=was, new=now)
            after = [_outside(line, new_asset, swap) for line in after]

        lines = _changed(before, after)
        if lines:
            _write_lines(path, after)
            changes.append(FileChange(name, lines))
    return changes


def repoint_lines(lines, url, sha="", version="", anchor="", old_url="",
                  old_version=""):
    """A recipe's lines moved onto a new source: its url, checksum and version.

    The source line is found by `anchor`, a regex whose first group is what
    to keep in front of the url, or by `old_url` appearing in it. Where
    `old_version` is given, other lines naming a url have it swapped too.
    """
    out = []
    for line in lines:
        found = re.match(anchor, line) if anchor else None
        if version and re.match(r"^version:", line):
            line = f"version: '{version}'"
        elif found:
            prefix = found.group(1) if found.re.groups else found.group(0)
            line = f"{prefix}{url}"
        elif old_url and re.match(r"^\s*source:\s", line) and old_url in line:
            line = line.replace(old_url, url)
        elif sha and re.match(r"^\s*source-checksum:\s*sha256/", line):
            line = re.sub(r"(sha256/).*", lambda m: m.group(1) + sha, line)
        elif old_version and "http" in line:
            line = replace_version(line, old_version, version)
        out.append(line)
    return out


def repoint_yaml(path, anchor, url, sha, version=""):
    """Point a snapcraft part on disk at a new source tarball and checksum."""
    path = Path(path)
    before = _read_lines(path)
    after = repoint_lines(before, url, sha, version, anchor=anchor)

    lines = _changed(before, after)
    if not lines:
        return []
    _write_lines(path, after)
    return [FileChange(f"{path.parent.name}/{path.name}", lines)]
