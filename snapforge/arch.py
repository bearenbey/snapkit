"""Which architecture this is, and what upstreams call it."""

import functools
import os
import platform
import re
import shutil
import subprocess

# The Debian name, which snapcraft uses too, and the spellings that turn up in
# release filenames. The first of each is the one to print.
SPELLINGS = {
    "amd64": ("amd64", "x86_64", "x86-64", "x8664", "x64", "linux64", "64bit"),
    "arm64": ("arm64", "aarch64", "armv8l", "armv8"),
    "armhf": ("armhf", "armv7l", "armv7", "armv6l", "armv6", "arm"),
    "i386": ("i386", "i486", "i586", "i686", "ia32", "x86", "32bit"),
    "ppc64el": ("ppc64el", "ppc64le", "powerpc64le"),
    "riscv64": ("riscv64", "riscv"),
    "s390x": ("s390x",),
    "loong64": ("loong64", "loongarch64"),
}

# What uname says, for the hosts where dpkg is not installed to be asked.
FROM_MACHINE = {
    "x86_64": "amd64", "amd64": "amd64",
    "aarch64": "arm64", "arm64": "arm64", "armv8l": "arm64",
    "armv7l": "armhf", "armv6l": "armhf", "arm": "armhf",
    "i386": "i386", "i486": "i386", "i586": "i386", "i686": "i386",
    "ppc64le": "ppc64el", "riscv64": "riscv64", "s390x": "s390x",
    "loongarch64": "loong64",
}

# Architectures nothing is built for here, whatever this machine is.
NEVER = ("mips64el", "mips64", "mipsel", "mips", "sparc64", "sparc", "m68k",
         "alpha", "hppa", "sh4", "powerpc64", "powerpc", "ppc64", "ppc",
         "armel", "universal")

# The environment variable that overrides all of it.
OVERRIDE = "SNAPKIT_ARCH"


class UnknownArchitecture(ValueError):
    """SNAPKIT_ARCH names something this does not know how to look for."""


def host():
    """The Debian name of the architecture being built for."""
    named = os.environ.get(OVERRIDE, "").strip().lower()
    if not named:
        return detected()
    # `uname -m` spellings are what a person reaches for, so take them; but
    # a name nothing recognises would quietly make every asset foreign, which
    # is the failure this whole module exists to prevent. Say so instead.
    wanted = FROM_MACHINE.get(named, named)
    if not known(wanted):
        raise UnknownArchitecture(
            f"{OVERRIDE}={named} is not an architecture this knows: "
            f"{', '.join(sorted(SPELLINGS))}")
    return wanted


@functools.lru_cache(maxsize=1)
def detected():
    """What this machine is, asked of dpkg first because snapd agrees with it."""
    if shutil.which("dpkg"):
        try:
            done = subprocess.run(["dpkg", "--print-architecture"],
                                  capture_output=True, text=True, timeout=5)
            if done.returncode == 0 and done.stdout.strip():
                return _from_machine(done.stdout)
        except (OSError, subprocess.SubprocessError):
            pass
    return _from_machine(platform.machine())


def _from_machine(machine):
    """A uname spelling as a Debian name, or itself when it is not a spelling."""
    machine = machine.strip().lower()
    return FROM_MACHINE.get(machine, machine)


def known(name):
    """Whether this is an architecture the spellings table knows about."""
    return name in SPELLINGS


def spellings(name):
    """What upstreams call this architecture in a filename."""
    return SPELLINGS.get(name, (name,))


def canonical(name):
    """The one spelling to print, which is the Debian name."""
    return spellings(name)[0]


@functools.lru_cache(maxsize=None)
def wanted(name):
    """Matches the spellings of the architecture being built for."""
    return _alternation(spellings(name))


@functools.lru_cache(maxsize=None)
def other(name):
    """Matches every architecture that is not the one being built for."""
    mine = set(spellings(name))
    words = [word for arch, spelled in SPELLINGS.items() if arch != name
             for word in spelled if word not in mine]
    return _alternation([w for w in words + list(NEVER) if w not in mine])


def _alternation(words):
    """One regex matching any of these words, and none of them inside another."""
    parts = []
    for word in sorted(set(words), key=len, reverse=True):
        # `x86` is 32-bit only when it is not the front of x86_64, and the
        # separator there is not a character the word boundary below rejects.
        parts.append(r"x86(?![_-]?64)" if word == "x86" else re.escape(word))
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])",
                      re.I)
