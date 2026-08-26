"""Rebuild the Unity Hub snap from the official upstream .deb.

`snapkit build unityhub` calls build() below with a Build; `snapkit update
unityhub` fetches the release it packs first.

This is a real snapcraft build -- snap/snapcraft.yaml is run as written -- so
what is left here is the work a recipe cannot express: reporting a version the
metadata disagrees with, catching the payload layout move that would otherwise
produce a snap which packs cleanly and dies on exec, and reporting host
libraries a classic snap will need at runtime and cannot bring with it.
"""

import pathlib
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
    deb = project.artifact("UnityHubSetup-amd64.deb")
    project.need_tools("dpkg-deb", "snapcraft", "unsquashfs")

    # A warning: the .deb is packed either way, the metadata has to follow.
    reported = project.capture("dpkg-deb", "-f", deb, "Version")
    if reported != project.version:
        project.warn(f"{project.yaml} says {project.version}, but {deb.name} is {reported}\n"
                     f"         bump the version in {project.yaml.name} to match")

    project.say(f"building Unity Hub {project.version}  (from {deb.name})")

    # No clean first: craft-parts re-pulls when the source changes.
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")

    built = project.directory / f"unityhub_{project.version}_{ARCH}.snap"
    if not built.is_file():
        project.die(f"build finished but {built.name} was not produced")

    # 3.20 moved this from /opt to /usr/lib; missing it entirely is fatal.
    project.say("checking the packed snap")
    root = unpacked(project, built)
    binary = next((root / candidate for candidate in
                   ("usr/lib/unityhub/unityhub-bin", "opt/unityhub/unityhub-bin")
                   if (root / candidate).is_file()), None)
    if binary is None:
        refuse(project, built, root.parent,
               f"no unityhub-bin in the packed snap: the payload layout changed")

    project.say("checking host libraries")
    project.warn_missing(binary,
                         "install them with: sudo apt install libgtk-3-0t64 libnotify4 \\"
                         "\n           libnss3 libxss1 libxtst6 libatspi2.0-0t64 libsecret-1-0")
    shutil.rmtree(root.parent, ignore_errors=True)

    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")
    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous --classic {built.name}")
    return built
