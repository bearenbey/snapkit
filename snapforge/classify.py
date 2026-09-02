"""What kind of thing a release actually ships."""

import re
from dataclasses import dataclass
from pathlib import Path

from . import arch

DEB = "deb"
ARCHIVE = "archive"
APPIMAGE = "appimage"

# Things that are about an asset rather than being one.
SIDECAR = (".zsync", ".asc", ".sig", ".sign", ".pem", ".cert", ".sha256",
           ".sha256sum", ".sha512", ".md5", ".blockmap", ".sbom", ".json",
           ".yml", ".yaml", ".txt", ".sum", ".intoto.jsonl")

# Packaging for somewhere that is not here.
FOREIGN = (".rpm", ".exe", ".msi", ".dmg", ".pkg", ".apk", ".ipa", ".snap",
           ".flatpak", ".pacman", ".ebuild", ".nupkg", ".jar", ".msix")

ARCHIVES = (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz",
            ".tbz2", ".tar.zst", ".tar", ".zip")

# Which architecture counts as ours is a question about this machine, so both
# of these are built per-host in arch.py rather than written out here.


def wanted_arch():
    """Matches the architecture being built for, however upstream spells it."""
    return arch.wanted(arch.host())


def other_arch():
    """Matches every architecture that is not the one being built for."""
    return arch.other(arch.host())

# A Windows build does not always say so: mpv ships -w64-mingw32.zip.
OTHER_OS = re.compile(
    r"(?<![a-z0-9])(?:windows|win32|win64|w64|w32|mingw32|mingw64|mingw|"
    r"msvc|cygwin|uwp|darwin|macos|osx|freebsd|netbsd|openbsd|android|"
    r"solaris|haiku)(?![a-z0-9])", re.I)

LINUX = ("linux", "gnu", "musl", "ubuntu", "debian")


@dataclass(frozen=True)
class Candidate:
    asset: object          # github.Asset
    kind: str
    score: int
    why: str

    @property
    def name(self):
        return self.asset.name


def _tokens(name):
    """The name split on every separator, so tokens can be matched as words."""
    return [t for t in re.split(r"[^A-Za-z0-9]+", name.lower()) if t]


def _has(name, words):
    """Whichever of `words` appears in the name as a word, or ""."""
    tokens = set(_tokens(name))
    for word in words:
        if word in tokens:
            return word
    return ""


def _match(name, pattern):
    """What `pattern` found in the name, or ""."""
    found = pattern.search(name)
    return found.group(0) if found else ""


def kind_of(name):
    """The shape of an asset, or "" if it is not one this tool can use."""
    lower = name.lower()
    if lower.endswith(SIDECAR) or lower.endswith(FOREIGN):
        return ""
    if lower.endswith(".deb"):
        return DEB
    if lower.endswith(".appimage"):
        return APPIMAGE
    if lower.endswith(ARCHIVES):
        return ARCHIVE
    return ""


def packages(directory):
    """Every file in a directory that is a shape this tool can package."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted((p for p in directory.iterdir()
                   if p.is_file() and kind_of(p.name)),
                  key=lambda p: p.name)


def rejection(name):
    """Why an asset is not a candidate, in words, or "" if it is one."""
    lower = name.lower()
    if lower.endswith(SIDECAR):
        return "a checksum or signature, not a payload"
    if lower.endswith(FOREIGN):
        return "packaging for somewhere else"
    foreign = _match(name, OTHER_OS)
    if foreign:
        return f"built for {foreign}"
    other = _match(name, other_arch())
    if other and not _match(name, wanted_arch()):
        return f"built for {other}"
    if not kind_of(name):
        return "not a package or an archive"
    return ""


def score(name):
    """How good a candidate an asset is, and one line saying why."""
    kind = kind_of(name)
    if not kind:
        return 0, kind, ""
    points = {DEB: 100, ARCHIVE: 80, APPIMAGE: 60}[kind]
    reasons = {DEB: "a Debian package: carries its own desktop entry and icon",
               ARCHIVE: "a prebuilt archive",
               APPIMAGE: "an AppImage, which has to be unpacked first"}
    why = [reasons[kind]]

    spelled = _match(name, wanted_arch())
    if spelled:
        points += 25
        why.append(f"{arch.canonical(arch.host())} ({spelled})")
    elif not _match(name, other_arch()):
        # Names no architecture at all. Usually means the only build there is.
        points += 10
        why.append("no architecture in the name")

    tokens = set(_tokens(name))
    linux = next((word for word in LINUX if word in tokens), "")
    if linux:
        points += 15
        why.append(linux)
    if "musl" in tokens:
        points += 10
        why.append("statically linked against musl")
    if "static" in tokens:
        points += 8
        why.append("static")
    return points, kind, ", ".join(why)


def strip_suffix(name):
    """A filename with whatever package extension it carries taken off."""
    for suffix in sorted(ARCHIVES + (".deb", ".appimage"), key=len, reverse=True):
        if name.lower().endswith(suffix):
            return name[:-len(suffix)]
    return name


def leading_name(name):
    """The part of a filename before the version starts."""
    stem = strip_suffix(name)
    found = re.search(r"[-_.](?:v?\d)", stem)
    return (stem[:found.start()] if found else stem).lower()


def classify(assets, wanted=""):
    """Every usable asset in a release, best first."""
    found = []
    for asset in assets:
        if rejection(asset.name):
            continue
        points, kind, why = score(asset.name)
        if points:
            found.append(Candidate(asset=asset, kind=kind, score=points, why=why))
    # A release can attach a companion package that scores exactly as well as
    # the application: clamui ships clamui-privileged-helper beside clamui,
    # and on the name alone the helper sorted first.
    target = (wanted or "").lower()
    return sorted(found, key=lambda c: (-c.score,
                                        leading_name(c.name) != target,
                                        len(c.name), c.name))


def rejected(assets):
    """The assets that were passed over, with the reason, for a full view."""
    passed_over = ((asset, rejection(asset.name)) for asset in assets)
    return [(asset, why) for asset, why in passed_over if why]


def spellings_of(version):
    """Every way a version can be written into a filename, longest first.

    Upstreams are not consistent about it: the tag says 1.2.3 and the file
    says 1_2_3, so a pattern built from the tag alone stops matching.
    """
    written = {version, version.replace("-", "_"), version.replace(".", "_"),
               version.replace("-", "."), version.replace("_", "-")}
    return sorted(filter(None, written), key=len, reverse=True)


def asset_pattern(name, version):
    """A regex matching this asset in *later* releases too."""
    pattern = re.escape(name)
    for spelling in spellings_of(version):
        pattern = pattern.replace(re.escape(spelling), r"[0-9][0-9A-Za-z.+~_-]*")
    return f"^{pattern}$"


def match_pattern(assets, pattern):
    """The asset of a later release that the stored pattern points at."""
    compiled = re.compile(pattern)
    for asset in assets:
        if compiled.match(asset.name):
            return asset
    return None
