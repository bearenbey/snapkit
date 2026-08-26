"""Rebuild the Unity Hub snap from the official upstream .deb."""

import pathlib
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
