"""Rebuild the Helium snap against an upstream helium-linux release.

`snapkit build helium` calls build() below with a Build; `snapkit update
helium` resolves the tag, downloads the .deb, rewrites snapcraft.yaml and
README.md and drops the superseded .deb first, for this project the same way
it does for the other twenty.

Unlike most of the projects here this one is a real snapcraft build -- the
recipe is run as written -- so what is left below is the part that is
Helium's alone: lifting the desktop entry and icon out of the .deb rather
than keeping a stale copy of them in the repository, and refusing to pack a
snap whose payload is not the release the recipe claims.
"""

import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

ARCH = "amd64"
GUI = Path("snap/gui")
# What to lift out of the .deb, and what it is called in snap/gui.
FROM_DEB = {
    "./usr/share/applications/helium.desktop": "helium.desktop",
    "./usr/share/icons/hicolor/256x256/apps/helium.png": "helium.png",
}


def packaged_deb(project):
    """The .deb the recipe names, which is the one to open.

    Read back rather than assumed: after an update, snapcraft.yaml is the
    record of which release this is.
    """
    for line in project.snapcraft_yaml.read_text().splitlines():
        found = re.match(r"^\s*source:\s*(helium-bin_.*\.deb)\s*$", line)
        if found:
            return project.directory / found.group(1)
    project.die(f"could not read the .deb source: from {project.snapcraft_yaml}")


def refresh_gui(project, deb):
    """Take the desktop entry and icon from the .deb rather than keeping a
    stale copy of them in the repository."""
    project.say(f"refreshing {GUI} from the .deb")
    GUI.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # Two files out of a tar stream, so read through rather than unpack.
        stream = subprocess.Popen(["dpkg-deb", "--fsys-tarfile", str(deb)],
                                  stdout=subprocess.PIPE)
        with tarfile.open(fileobj=stream.stdout, mode="r|*") as tar:
            for member in tar:
                if member.name in FROM_DEB:
                    tar.extract(member, tmp, filter="data")
        stream.stdout.close()
        stream.wait()

        for inside, name in FROM_DEB.items():
            source = tmp / inside
            if not source.is_file():
                project.die(f"{deb.name} has no {inside}: the payload layout changed")
            if name.endswith(".desktop"):
                # The snap's icon is meta/gui/helium.png, not the hicolor tree.
                source.write_text(re.sub(
                    r"(?m)^Icon=.*$", "Icon=${SNAP}/meta/gui/helium.png",
                    source.read_text()))
            destination = GUI / name
            if destination.is_file() and destination.read_bytes() == source.read_bytes():
                project.note(f"{destination} unchanged")
            else:
                project.copy(source, destination)
                project.note(f"{destination} updated")


def build(project):
    project.need_tools("dpkg-deb", "tar", "snapcraft")

    deb = packaged_deb(project)
    if not deb.is_file():
        project.die(f"{project.snapcraft_yaml.name} wants {deb.name}, which is "
                    f"not here -- snapkit update helium --force fetches it")
    project.run("dpkg-deb", "-I", deb, capture_output=True)

    project.say(f"building Helium {project.version}  (from {deb.name})")
    refresh_gui(project, deb)

    # Stale parts leak into the pull step, and a browser is big enough to care.
    project.say("snapcraft clean")
    project.run("snapcraft", "clean")
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")

    built = project.directory / f"helium_{project.version}_{ARCH}.snap"
    if not built.is_file():
        project.die(f"build finished but {built.name} was not produced")
    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")

    # Neither interface auto-connects for a local --dangerous install.
    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous {built.name}\n"
                 f"      sudo snap connect helium:browser-sandbox\n"
                 f"      sudo snap connect helium:u2f-devices")
    return built
