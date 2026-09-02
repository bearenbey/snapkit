"""A package that is already on disk, rather than one attached to a release."""

import functools
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


def _declared(path, field, kind=""):
    """What a .deb says about itself, or "" for anything else and for a .deb
    that will not open."""
    if (kind or classify.kind_of(Path(path).name)) != classify.DEB:
        return ""
    try:
        return inspect.control_fields(path).get(field, "")
    except Exception:                                         # noqa: BLE001
        return ""


def version_of(path, kind=""):
    """The version this file is of, read from it where that is possible."""
    path = Path(path)
    return _declared(path, "Version", kind) or version_from("", path.name)


@functools.lru_cache(maxsize=None)
def _ends_the_name(spelled, missed):
    """Where the name stops and the download's description starts.

    Built per architecture rather than written out: which spellings are ours
    and which are somebody else's is a question about the host.
    """
    return re.compile(
        r"(?<![a-z0-9])(?:v?\d+(?:[._]\d+)+|\d{2,}|"
        + "|".join(classify.LINUX) + r")(?![a-z0-9])"
        r"|" + spelled + r"|" + missed + r"|" + classify.OTHER_OS.pattern, re.I)


def name_from(path, kind=""):
    """What the program in this file is called."""
    path = Path(path)
    declared = _declared(path, "Package", kind)
    if declared:
        return declared

    stem = classify.strip_suffix(path.name)
    found = _ends_the_name(classify.wanted_arch().pattern,
                           classify.other_arch().pattern).search(stem)
    if found and found.start():
        stem = stem[:found.start()]
    return stem.strip("-_. ")


def glob_for(name, version):
    """A shell glob matching this file in every version of it."""
    if not version:
        return name
    glob = name
    for spelling in classify.spellings_of(version):
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
