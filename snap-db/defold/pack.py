"""Rebuild the Defold snap from the official upstream editor zip.

`snapkit build defold` calls build() below with a Build; `snapkit update
defold` fetches the release it packs first.

This is a real snapcraft build -- snap/snapcraft.yaml is run as written -- so
what is left here is the work a recipe cannot express: refusing to ship a snap
whose payload is not the release the recipe claims, and checking that the
OpenAL the game engine loads still needs only what the base and the platform
snaps provide.

libopenal used to be three .debs downloaded into vendor/ and unpacked by hand,
because there was no build backend on the machine this was written on. The
recipe now stage-packages them from the archive against the same noble base,
so the pinning is no longer this repository's to carry.
"""

import pathlib
import re
import shutil
import subprocess
import tempfile

ARCH = "amd64"

# What libopenal may ask for; the base and platform snaps cover the rest.
EXPECTED_NEEDED = {
    "libsndio.so.7", "libstdc++.so.6", "libm.so.6",
    "libgcc_s.so.1", "libc.so.6", "ld-linux-x86-64.so.2",
    "libasound.so.2", "libpthread.so.0", "libdl.so.2", "librt.so.1",
}


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


def editor_version(root):
    """The version out of the editor's own launcher config."""
    config = root / "opt" / "Defold" / "config"
    if not config.is_file():
        return None
    for line in config.read_text().splitlines():
        if line.startswith("version ="):
            return line.split(" = ", 1)[1]
    return None


def check_openal(project, root):
    """Warn if libopenal grew a dependency nothing in the snap provides.

    A missing NEEDED here means a library the engine loads stopped being
    covered by the provider snaps, which only shows up as a broken game at
    runtime rather than as a failed build.
    """
    library = root / "usr/lib/x86_64-linux-gnu/libopenal.so.1"
    if not library.is_file():
        project.warn("no libopenal.so.1 in the packed snap; "
                     "the engine's audio will not load")
        return
    dump = subprocess.run(["objdump", "-p", str(library)],
                          capture_output=True, text=True).stdout
    needed = set(re.findall(r"NEEDED\s+(\S+)", dump))
    unexpected = sorted(needed - EXPECTED_NEEDED)
    if unexpected:
        project.warn(f"libopenal.so.1 now needs {' '.join(unexpected)}, which "
                     f"nothing in the snap or the platform snaps provides")


def build(project):
    zip_path = project.artifact("Defold-x86_64-linux.zip")
    project.need_tools("snapcraft", "objdump", "unsquashfs")

    project.say(f"building Defold {project.version}  (from {zip_path.name})")

    # No clean first: craft-parts re-pulls when the source changes.
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")

    built = project.directory / f"defold_{project.version}_{ARCH}.snap"
    if not built.is_file():
        project.die(f"build finished but {built.name} was not produced")

    project.say("checking the packed snap")
    root = unpacked(project, built)

    # Or the snap advertises a version its payload does not have.
    reported = editor_version(root)
    if reported is None:
        refuse(project, built, root.parent,
               "no version in opt/Defold/config: the zip layout changed")
    if reported != project.version:
        refuse(project, built, root.parent,
               f"version mismatch: snapcraft.yaml says {project.version}, "
               f"the packed editor reports {reported}")

    project.say("checking the engine's audio libraries")
    check_openal(project, root)
    shutil.rmtree(root.parent, ignore_errors=True)

    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")
    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous {built.name}")
    return built
