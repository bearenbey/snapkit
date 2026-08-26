"""Rebuild the Floorp snap from the official upstream Linux tarball.

`snapkit build floorp` calls build() below with a Build; `snapkit update
floorp` fetches the release it packs first.

This is a real snapcraft build -- snap/snapcraft.yaml is run as written -- so
what is left here is the work a recipe cannot express: refusing to ship a snap
whose payload is not the release the recipe claims, and reporting a library
the payload needs that neither the base nor the platform snaps provide.
"""

import configparser
import pathlib
import re
import shutil
import subprocess
import tempfile

ARCH = "amd64"
# Where the tarball lands; the launcher and policies overlay spell it too.
APP = "usr/lib/floorp"
# A strict snap links against core24 and the platform snaps mounted into it.
PLATFORMS = ("/snap/core24/current", "/snap/gnome-46-2404/current",
             "/snap/mesa-2404/current")


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


def platform_sonames():
    """Every library name the base and the two platform snaps offer.

    Not `ldd`: this snap is strictly confined, so what it links against at
    runtime is core24 plus the content snaps mounted into it, and the host's
    own /usr/lib has nothing to do with it. A platform snap that is not
    installed here is skipped -- it says nothing about the snap being packed,
    only about this machine.
    """
    names = set()
    for root in PLATFORMS:
        root = pathlib.Path(root)
        if root.is_dir():
            names.update(path.name for path in root.rglob("*.so*"))
    return names


def check_platform_libraries(project, app, binary):
    """Warn about anything the payload links against that nothing provides.

    A missing soname fails when the browser is started rather than when it is
    packed, where it reads as a bare exec failure, so it is worth saying here.
    A warning and not an error: it is about which platform snaps happen to be
    installed on this machine.
    """
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


def read_application_ini(app):
    """The release the payload holds, as (version, gecko), or (None, None).

    Gecko spells its own version into application.ini as
    `<app version>@<platform version>` -- 12.17.0@154.0 -- so the Floorp
    release and the Firefox it is built on are both readable from it.
    """
    path = app / "application.ini"
    ini = configparser.ConfigParser(interpolation=None)
    if not ini.read(path):
        return None, None
    try:
        version = ini["App"]["Version"]
    except KeyError:
        return None, None
    release, _, gecko = version.partition("@")
    return release, gecko or ini.get("Gecko", "MinVersion", fallback="?")


def build(project):
    tarball = project.artifact("floorp-linux-x86_64.tar.xz")
    project.need_tools("snapcraft", "objdump", "unsquashfs")

    project.say(f"building Floorp {project.version}  (from {tarball.name})")

    # No clean first: craft-parts re-pulls when the source changes.
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")

    built = project.directory / f"floorp_{project.version}_{ARCH}.snap"
    if not built.is_file():
        project.die(f"build finished but {built.name} was not produced")

    project.say("checking the packed snap")
    root = unpacked(project, built)
    app = root / APP
    if not (app / "floorp").is_file():
        refuse(project, built, root.parent,
               f"no {APP}/floorp in the packed snap: the tarball layout changed")

    # Or the snap advertises a version its payload does not have.
    reported, gecko = read_application_ini(app)
    if reported is None:
        refuse(project, built, root.parent,
               f"no [App] Version in {APP}/application.ini: "
               f"the tarball layout changed")
    if reported != project.version:
        refuse(project, built, root.parent,
               f"version mismatch: snapcraft.yaml says {project.version}, "
               f"the packed payload reports {reported}")
    project.note(f"Gecko {gecko}")

    project.say("checking platform libraries")
    check_platform_libraries(project, app, app / "floorp")
    shutil.rmtree(root.parent, ignore_errors=True)

    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")
    # browser-sandbox does not auto-connect for a local --dangerous install.
    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous {built.name}\n"
                 f"      sudo snap connect floorp:browser-sandbox\n"
                 f"      sudo snap connect floorp:u2f-devices")
    return built
