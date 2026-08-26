"""What kind of thing a release actually ships."""

import re
from dataclasses import dataclass
from pathlib import Path

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

# Matched on boundaries: splitting turns x86_64 into x86 and reads as 32-bit.
WANTED_ARCH = re.compile(
    r"(?<![a-z0-9])(?:amd64|x86[_-]?64|x8664|x64|linux64|64bit)(?![a-z0-9])", re.I)

# `x86` is only 32-bit when it is not the front of x86_64, hence the lookahead.
OTHER_ARCH = re.compile(
    r"(?<![a-z0-9])(?:"
    r"aarch64|arm64|armv[0-9]+[a-z]?|armhf|armel|arm|"
    r"i[3-6]86|x86(?![_-]?64)|ia32|32bit|"
    r"riscv64|riscv|ppc64le|ppc64|powerpc64|powerpc|ppc|s390x|"
    r"mips64el|mips64|mipsel|mips|m68k|sparc64|sparc|"
    r"loongarch64|loong64|alpha|hppa|sh4|universal"
    r")(?![a-z0-9])", re.I)

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
    other = _match(name, OTHER_ARCH)
    if other and not _match(name, WANTED_ARCH):
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

    arch = _match(name, WANTED_ARCH)
    if arch:
        points += 25
        why.append(f"x86_64 ({arch})")
    elif not _match(name, OTHER_ARCH):
        # Names no architecture at all. Usually means the only build there is.
        points += 10
        why.append("no architecture in the name")

    linux = _has(name, LINUX)
    if linux:
        points += 15
        why.append(linux)
    if "musl" in _tokens(name):
        points += 10
        why.append("statically linked against musl")
    if "static" in _tokens(name):
        points += 8
        why.append("static")
    return points, kind, ", ".join(why)


def classify(assets):
    """Every usable asset in a release, best first."""
    found = []
    for asset in assets:
        if rejection(asset.name):
            continue
        points, kind, why = score(asset.name)
        if points:
            found.append(Candidate(asset=asset, kind=kind, score=points, why=why))
    return sorted(found, key=lambda c: (-c.score, c.name))


def rejected(assets):
    """The assets that were passed over, with the reason, for a full view."""
    return [(asset, rejection(asset.name)) for asset in assets
            if rejection(asset.name)]


def asset_pattern(name, version):
    """A regex matching this asset in *later* releases too."""
    pattern = re.escape(name)
    spellings = {version, version.replace("-", "_"), version.replace(".", "_"),
                 version.replace("-", "."), version.replace("_", "-")}
    for spelling in sorted(filter(None, spellings), key=len, reverse=True):
        pattern = pattern.replace(re.escape(spelling), r"[0-9][0-9A-Za-z.+~_-]*")
    return f"^{pattern}$"


def match_pattern(assets, pattern):
    """The asset of a later release that the stored pattern points at."""
    compiled = re.compile(pattern)
    for asset in assets:
        if compiled.match(asset.name):
            return asset
    return None
