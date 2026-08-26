"""Rebuild the btop snap from the official upstream release tarball.

`snapkit build btop` calls build() below with a Build; `snapkit update btop`
fetches the release it packs first.

This is a real snapcraft build -- snap/snapcraft.yaml is run as written -- so
what is left here is the work a recipe cannot express: refusing to pack a snap
whose payload is not the release the recipe claims.

This file used to assemble prime/ itself, reimplementing the dump plugin's
organize/stage rules and rendering meta/snap.yaml from the recipe, because
there was no build backend on the machine it was written on. That
reimplementation is what made the recipe wrong without anyone noticing: it
extracted the tarball whole, while craft-parts strips an archive's single
leading directory, so the `organize` map was written against paths snapcraft
never sees and `snapcraft pack` failed at the staging step. The recipe is
correct now and snapcraft runs it.
"""

import pathlib
import re
import shutil
import tempfile

ARCH = "amd64"


def unpacked(project, snap):
    """The packed snap's contents, extracted to a temporary directory.

    snapcraft builds in a managed instance and its parts/, stage/ and prime/
    live inside it, so there is no prime/ on this side to look at. The checks
    below therefore read the artifact that was actually produced, which is the
    stronger thing to check anyway: it is what ships.
    """
    out = pathlib.Path(tempfile.mkdtemp(prefix="snapkit-check-"))
    project.run("unsquashfs", "-q", "-d", out / "root", snap)
    return out / "root"


def refuse(project, snap, holding, message):
    """Delete a snap that failed its checks, then say why.

    Packed and then rejected rather than rejected before packing: what is worth
    checking is only in the artifact. Leaving it on disk would let the next
    `snapkit check` read it as a good build of this version.
    """
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
