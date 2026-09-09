"""Rebuild the Helium snap against an upstream helium-linux release."""

import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

GUI = Path("snap/gui")
# What to lift out of the .deb, and what it is called in snap/gui.
FROM_DEB = {
    "./usr/share/applications/helium.desktop": "helium.desktop",
    "./usr/share/icons/hicolor/256x256/apps/helium.png": "helium.png",
}


def refresh_gui(project, deb):
    """Take the desktop entry and icon from the .deb rather than keeping a copy."""
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
    project.need_tools("dpkg-deb", "tar")
    # The recipe's own source:, so a superseded .deb beside it is not opened.
    deb = project.artifact("helium-bin_*.deb")
    project.run("dpkg-deb", "-I", deb, capture_output=True)

    project.say(f"building Helium {project.version}  (from {deb.name})")
    refresh_gui(project, deb)

    # Stale parts leak into the pull step, and a browser is big enough to care.
    built = project.pack(clean=True)

    # Neither interface auto-connects for a local --dangerous install.
    return project.finish(built, "browser-sandbox", "u2f-devices")
