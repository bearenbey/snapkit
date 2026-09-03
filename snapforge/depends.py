"""What a payload needs at runtime, and which of it the snap has to stage."""

import functools
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import elf, platform

# A Depends: names daemons and tools too, and a snap stages neither.
NOT_A_LIBRARY = re.compile(r"-(?:bin|dev|doc|utils|tools|dbg|common)$")

# Not libraries, but a snap that talks to the network or draws text wants them.
WANTED_ANYWAY = frozenset("ca-certificates fonts-liberation".split())


@dataclass
class Needs:
    """What a payload turned out to want, and where each part comes from."""

    packages: list = field(default_factory=list)      # stage-packages, sorted
    bundled: list = field(default_factory=list)       # shipped in the payload
    from_host: list = field(default_factory=list)     # a driver interface's
    unresolved: list = field(default_factory=list)    # needed, no package known
    unverified: list = field(default_factory=list)    # named by the deb, unchecked

    @property
    def complete(self):
        """Whether every library the payload asks for was accounted for."""
        return not self.unresolved


def parse_depends(text):
    """The package names in a Debian `Depends:` field, alternatives resolved."""
    found = []
    for clause in (text or "").split(","):
        options = [one for one in (_one_name(o) for o in clause.split("|")) if one]
        if options:
            found.append(_preferred(options))
    return found


def _one_name(text):
    """One alternative, without its version, architecture or build profile."""
    name = re.split(r"[\s(\[<]", text.strip(), 1)[0].strip().split(":")[0]
    return name if re.match(r"^[a-z0-9][a-z0-9.+-]*$", name) else ""


def _preferred(options):
    """Of `a | b`, the one core24 has, which is not the first often enough."""
    known = _verified()
    for option in options:
        if option in known or noble(option) in known:
            return option
    # Nothing recognised either of them, so the packager's order stands.
    return options[0]


def noble(package):
    """A package under the name noble gives it, which is not always Debian's."""
    return package + "t64" if package in platform.RENAMED_T64 else package


def supplied(gui):
    """Every soname this snap gets free, which the extension changes."""
    return platform.BASE | (platform.GNOME if gui else frozenset())


def bundled_libraries(root):
    """The libraries a payload ships for itself, by the name others ask for."""
    found = {}
    for path in Path(root).rglob("*.so*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            name = elf.soname_of(path) or path.name
        except elf.NotAnELF:
            continue
        found.setdefault(name, path)
        found.setdefault(path.name, path)
    return found


def wanted(root, command, gui=False):
    """Every soname the program reaches for, following what it ships itself."""
    root = Path(root)
    start = root / command if command else None
    if start is None or not start.is_file():
        return set(), {}
    inside = bundled_libraries(root)
    seen, queue, asked = set(), [start], set()
    while queue:
        binary = queue.pop()
        try:
            names = elf.needed(binary)
        except elf.NotAnELF:
            continue
        for soname in names:
            if soname in seen:
                continue
            seen.add(soname)
            asked.add(soname)
            # Brought with it, but what that needs is still ours to find.
            if soname in inside:
                queue.append(inside[soname])
    return asked, inside


def resolve(root="", command="", gui=False, control=None):
    """What to stage for this payload, and what could not be accounted for."""
    control = control or {}
    packages, bundled, host, unknown = set(), set(), set(), set()
    unchecked = set()
    here = supplied(gui)

    asked, inside = wanted(root, command, gui) if root else (set(), {})
    for soname in sorted(asked):
        if soname in here:
            continue
        if soname in platform.FROM_THE_HOST:
            host.add(soname)
        elif soname in inside:
            bundled.add(soname)
        elif soname in platform.PACKAGE_OF:
            packages.add(platform.PACKAGE_OF[soname])
        else:
            unknown.add(soname)

    # A packager knows what the binary does not say: a plugin opened later.
    covered = set(platform.SUPPLIED_BY_BASE)
    if gui:
        covered |= platform.SUPPLIED_BY_GNOME
    lowered = {name.lower() for name in here}
    # Where the binary and the packaging name one library, the binary wins.
    families = {_family(one) for one in packages}
    for package in parse_depends(control.get("Depends", "")):
        if package in covered or not _is_library(package):
            continue
        renamed = noble(package)
        if renamed in covered or _looks_supplied(renamed, lowered):
            continue
        if _family(renamed) not in families:
            packages.add(renamed)
            # The deb names what its own release had, which may have moved.
            if renamed not in _verified():
                unchecked.add(renamed)

    return Needs(packages=sorted(packages), bundled=sorted(bundled),
                 from_host=sorted(host), unresolved=sorted(unknown),
                 unverified=sorted(unchecked & set(packages)))


def _looks_supplied(package, lowered):
    """A last look for a package whose library the platform turns out to have."""
    stem = package[: -len("t64")] if package.endswith("t64") else package
    found = re.match(r"^(.+?)-?(\d+)$", stem)
    if not found:
        return False
    # Wrong here is one package too many; wrong the other way does not build.
    return f"{found.group(1)}.so.{found.group(2)}" in lowered


@functools.lru_cache(maxsize=1)
def _verified():
    """Package names something has actually seen, as opposed to been told."""
    return frozenset(set(platform.PACKAGE_OF.values())
                     | platform.SUPPLIED_BY_BASE | platform.SUPPLIED_BY_GNOME)


def _is_library(package):
    """Whether this is a package of libraries rather than a program."""
    if package in WANTED_ANYWAY:
        return True
    if package.startswith("gir1.2-"):
        return False
    return package.startswith("lib") and not NOT_A_LIBRARY.search(package)


def _family(package):
    """A name without its soname number, so libmpv1 and libmpv2 meet."""
    stem = package[: -len("t64")] if package.endswith("t64") else package
    return re.sub(r"-?\d+(?:\.\d+)*$", "", stem)
