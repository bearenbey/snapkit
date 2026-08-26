"""A package that is already on disk, rather than one attached to a release."""

import re
from dataclasses import dataclass
from pathlib import Path

from . import classify, inspect
from .adopt import version_from
from .versions import deb_key, version_key

@dataclass
class Found:
    """One package file in a directory, and what can be said about it."""

    path: Path
    kind: str
    version: str
    score: int
    why: str

    @property
    def name(self):
        return self.path.name


def version_of(path, kind=""):
    """The version this file is of, read from it where that is possible."""
    path = Path(path)
    kind = kind or classify.kind_of(path.name)
    if kind == classify.DEB:
        try:
            found = inspect.control_fields(path).get("Version", "")
        except Exception:                                     # noqa: BLE001
            found = ""
        if found:
            return found
    return version_from("", path.name)


# Where the name stops and the download's description starts.
_ENDS_THE_NAME = re.compile(
    r"(?<![a-z0-9])(?:v?\d+(?:[._]\d+)+|\d{2,}|"
    + "|".join(classify.LINUX) + r")(?![a-z0-9])"
    r"|" + classify.WANTED_ARCH.pattern + r"|" + classify.OTHER_ARCH.pattern
    + r"|" + classify.OTHER_OS.pattern, re.I)


def name_from(path, kind=""):
    """What the program in this file is called."""
    path = Path(path)
    kind = kind or classify.kind_of(path.name)
    if kind == classify.DEB:
        try:
            declared = inspect.control_fields(path).get("Package", "")
        except Exception:                                     # noqa: BLE001
            declared = ""
        if declared:
            return declared

    stem = path.name
    for suffix in sorted(classify.ARCHIVES + (".deb", ".appimage"),
                         key=len, reverse=True):
        if stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    found = _ENDS_THE_NAME.search(stem)
    if found and found.start():
        stem = stem[:found.start()]
    return stem.strip("-_. ")


def glob_for(name, version):
    """A shell glob matching this file in every version of it."""
    if not version:
        return name
    spellings = {version, version.replace("-", "_"), version.replace(".", "_"),
                 version.replace("-", ".")}
    glob = name
    for spelling in sorted(filter(None, spellings), key=len, reverse=True):
        glob = glob.replace(spelling, "*")
    # Collapse adjacent runs: one wildcard will do for app-1.2.3/app-1.2.3.deb.
    return re.sub(r"\*{2,}", "*", glob)


def describe(path):
    """One package file, classified the way a release asset would be."""
    path = Path(path)
    points, kind, why = classify.score(path.name)
    if not kind:
        return None
    return Found(path=path, kind=kind, version=version_of(path, kind),
                 score=points, why=why)


def find(directory, pattern=None):
    """Every package file in a directory, best first."""
    directory = Path(directory)
    here = (sorted(p for p in directory.glob(pattern) if p.is_file())
            if pattern else classify.packages(directory))
    found = [describe(path) for path in here
             if not classify.rejection(path.name)]
    return sorted((f for f in found if f), key=lambda f: (-f.score, f.name))


def newest(directory, pattern=None):
    """The one file in a directory an update should be measured against."""
    found = find(directory, pattern)
    if not found:
        return None
    best = [f for f in found if f.score == found[0].score]
    if best[0].kind == classify.DEB:
        return max(best, key=lambda f: deb_key(f.version or "0"))
    return max(best, key=lambda f: version_key(f.version or "0"))


def looks_like_path(text):
    """Whether what was typed names a file or directory rather than a repo."""
    if not text:
        return False
    if text.startswith(("http://", "https://", "git@")):
        return False
    expanded = Path(text).expanduser()
    return expanded.exists()
