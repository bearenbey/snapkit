"""A package that is already on disk, rather than one attached to a release.

Not everything worth packaging is published as a GitHub release, and not
every upstream that publishes one publishes it anywhere this tool can follow.
Discord answers a download endpoint with a redirect; Unity and Signal publish
into apt repositories; plenty of projects just hand you a `.deb`. For those,
the file itself is the thing there is to work with -- so it can be pointed at
directly:

    snapkit create ./discord-1.0.155.deb
    snapkit create ~/Downloads              # asks which of what is in there

What happens then is what happens for a release asset, minus the download: it
is opened, the program and its desktop entry and icon are found, a recipe is
written around them, and it is registered. The recipe names the file rather
than a URL, so snapcraft stages what is sitting beside it.

A snap made this way can still be kept up to date, against the directory
rather than against an upstream:

    "upstream": {"kind": "local", "glob": "discord-*.deb"}

Drop a newer `.deb` in beside the project and `snapkit check` says so, the
same way it does for a repository -- see the `local` shape in sources.py.
That is the whole of what tracking means here: this tool cannot know where a
file came from, so it does not pretend to, and what it can see is the folder.
"""

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
    """The version this file is of, read from it where that is possible.

    A `.deb` states its version in its control file, which is authoritative.
    Everything else has only its name to go on -- which is why an archive
    whose name carries no version comes back empty rather than guessed at.
    """
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
    """What the program in this file is called.

    A `.deb` says so itself, in the `Package` field of its control file, and
    that is the answer -- `sublime-text_build-4200_amd64.deb` is `sublime-text`,
    which no amount of splitting the file name apart reliably gives you.

    For everything else the file name is all there is, so the question becomes
    where the name stops and the description of the download begins. Cutting
    at the first separator is what this did first and it is wrong on any name
    with a hyphen in it -- `ungoogled-chromium-151...` came out as
    `ungoogled`. Cutting at the first version, architecture or platform word
    keeps the whole name and drops the rest.
    """
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
    """A shell glob matching this file in every version of it.

    The counterpart of `classify.asset_pattern`, which answers with a regex
    for matching release assets. This one has to be a glob because it is what
    goes in the record as `asset_glob`, where the build and the cleanup both
    read it as one.
    """
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
    """Every package file in a directory, best first.

    Best is the classifier's ordering -- a `.deb` over an archive over an
    AppImage, and anything built for another architecture or another
    operating system filed out before it gets here -- with the newest version
    winning between two of the same shape, because a folder someone has been
    downloading into has last month's copy in it too.
    """
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
    """Whether what was typed names a file or directory rather than a repo.

    Asked before the text is parsed as `owner/name`, because `./a/b` and
    `a/b` are both two segments with a slash between them and only one of
    them is on the disk. Existence decides it: a path that is there is a
    path, and anything else is a repository until GitHub says otherwise.
    """
    if not text:
        return False
    if text.startswith(("http://", "https://", "git@")):
        return False
    expanded = Path(text).expanduser()
    return expanded.exists()
