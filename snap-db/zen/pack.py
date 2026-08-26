"""Rebuild the Zen Browser snap from the official upstream tarball."""

import pathlib
import re
import shutil
import subprocess
import tempfile

ARCH = "amd64"
# Where the tarball's zen/ tree lands, per snap/snapcraft.yaml's organize.
APP = "opt/zen"
# A strict snap links against core24 and the platform snaps mounted into it.
PLATFORMS = ("/snap/core24/current", "/snap/gnome-46-2404/current",
             "/snap/mesa-2404/current")


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


def platform_sonames():
    """Every library name the base and the two platform snaps offer."""
    names = set()
    for root in PLATFORMS:
        root = pathlib.Path(root)
        if root.is_dir():
            names.update(path.name for path in root.rglob("*.so*"))
    return names


def check_platform_libraries(project, app, binary):
    """Warn about anything the payload links against that nothing provides."""
    bundled = {path.name for path in app.rglob("*.so")}
    available = platform_sonames()
    if not available:
        project.warn("none of core24, gnome-46-2404 or mesa-2404 is installed here; "
                     "the payload's libraries were not checked")
        return {}

    missing = {}
    for candidate in sorted(app.rglob("*.so")) + [binary]:
        out = subprocess.run(["objdump", "-p", str(candidate)],
                             capture_output=True, text=True).stdout
        for soname in re.findall(r"NEEDED\s+(\S+)", out):
            if soname not in bundled and soname not in available:
                missing.setdefault(soname, []).append(candidate.name)
    for soname, users in sorted(missing.items()):
        project.warn(f"{soname} is in neither the base nor the platform snaps "
                     f"(needed by {', '.join(sorted(set(users)))})")
    return missing


def payload_version(app):
    """The version out of the payload's own application.ini."""
    ini = app / "application.ini"
    if not ini.is_file():
        return None
    section = ""
    for line in ini.read_text().splitlines():
        found = re.match(r"^\[(.*)\]\s*$", line)
        if found:
            section = found.group(1)
        elif section == "App":
            found = re.match(r"^Version\s*=\s*(\S+)\s*$", line)
            if found:
                return found.group(1)
    return None


def build(project):
    tarball = project.artifact("zen.linux-x86_64.tar.xz")
    project.need_tools("snapcraft", "objdump", "unsquashfs")

    project.say(f"building Zen {project.version}  (from {tarball.name})")

    # No clean first: craft-parts re-pulls when the source changes.
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")

    built = project.directory / f"zen_{project.version}_{ARCH}.snap"
    if not built.is_file():
        project.die(f"build finished but {built.name} was not produced")

    project.say("checking the packed snap")
    root = unpacked(project, built)
    app = root / APP
    if not (app / "zen").is_file():
        refuse(project, built, root.parent,
               f"no {APP}/zen in the packed snap: the tarball layout changed")

    # Or the snap advertises a version its payload does not have.
    reported = payload_version(app)
    if reported is None:
        refuse(project, built, root.parent,
               f"no [App] Version in {APP}/application.ini: "
               f"the tarball layout changed")
    if reported != project.version:
        refuse(project, built, root.parent,
               f"version mismatch: snapcraft.yaml says {project.version}, "
               f"the packed payload reports {reported}")

    project.say("checking platform libraries")
    check_platform_libraries(project, app, app / "zen")
    shutil.rmtree(root.parent, ignore_errors=True)

    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")
    # browser-sandbox does not auto-connect for a local --dangerous install.
    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous {built.name}\n"
                 f"      sudo snap connect zen:browser-sandbox\n"
                 f"      sudo snap connect zen:u2f-devices")
    return built
