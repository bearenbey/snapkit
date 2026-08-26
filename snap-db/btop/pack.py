"""Rebuild the btop snap from the official upstream release tarball."""

import pathlib
import re
import shutil
import tempfile

ARCH = "amd64"


def unpacked(project, snap):
    """The packed snap's contents, extracted to a temporary directory."""
    out = pathlib.Path(tempfile.mkdtemp(prefix="snapkit-check-"))
    project.run("unsquashfs", "-q", "-d", out / "root", snap)
    return out / "root"


def refuse(project, snap, holding, message):
    """Delete a snap that failed its checks, then say why."""
    shutil.rmtree(holding, ignore_errors=True)
    snap.unlink(missing_ok=True)
    project.die(message)


def build(project):
    tarball = project.artifact("btop-x86_64-unknown-linux-musl.tar.gz")
    project.need_tools("snapcraft", "unsquashfs")

    project.say(f"building btop {project.version}  (from {tarball.name})")

    # No clean first: craft-parts re-pulls when the source changes.
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")

    built = project.directory / f"btop_{project.version}_{ARCH}.snap"
    if not built.is_file():
        project.die(f"build finished but {built.name} was not produced")

    project.say("checking the packed snap")
    root = unpacked(project, built)
    binary = root / "bin" / "btop"
    if not binary.is_file():
        refuse(project, built, root.parent,
               "no bin/btop in the packed snap: the tarball layout changed")

    # First line only: the rest is compiler flags, and it is ANSI-bold.
    first = project.capture(binary, "--version").splitlines()[0]
    reported = re.sub(r"\x1b\[[0-9;]*m", "", first)
    reported = reported.split(":", 1)[-1].strip().split("+")[0].lstrip("v")
    if reported != project.version:
        refuse(project, built, root.parent,
               f"version mismatch: snapcraft.yaml says {project.version}, "
               f"the packed binary reports {reported}")
    shutil.rmtree(root.parent, ignore_errors=True)
    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")

    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous {built.name}\n"
                 f"      sudo snap connect btop:system-observe\n"
                 f"      sudo snap connect btop:process-control\n"
                 f"      sudo snap connect btop:hardware-observe\n"
                 f"      sudo snap connect btop:mount-observe\n"
                 f"      sudo snap connect btop:network-observe")
    return built
