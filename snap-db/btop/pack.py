"""Rebuild the btop snap from the official upstream release tarball."""

import re


def reported_version(project, binary):
    """What `btop --version` says, with the display stripped off it."""
    # First line only: the rest is compiler flags, and it is ANSI-bold.
    first = project.capture(binary, "--version").splitlines()[0]
    plain = re.sub(r"\x1b\[[0-9;]*m", "", first)
    return plain.split(":", 1)[-1].strip().split("+")[0].lstrip("v")


def build(project):
    tarball = project.artifact("btop-x86_64-unknown-linux-musl.tar.gz")
    project.say(f"building btop {project.version}  (from {tarball.name})")

    built = project.pack()
    with project.unpacked(built) as root:
        binary = root / "bin" / "btop"
        if not binary.is_file():
            project.die("no bin/btop in the packed snap: the tarball layout changed")
        project.check_version(reported_version(project, binary),
                              "the packed binary")

    return project.finish(built, "system-observe", "process-control",
                          "hardware-observe", "mount-observe", "network-observe")
