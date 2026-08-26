"""Rebuild the Transmission snap from the official upstream source release.

`snapkit build transmission` calls build() below with a Build; `snapkit update
transmission` fetches the release it packs first.

This is a real snapcraft build -- snap/snapcraft.yaml is run as written -- so
what is left here is the work a recipe cannot express: refusing to start a
ten-minute compile for a tarball that is not the release the recipe claims.

The compile used to happen in a noble build root assembled by
tools/fetch-buildroot.py and entered with bubblewrap, because there was no
build backend on the machine this was written on. snapcraft builds in a noble
instance against the same core24 base, so the build root, the sandbox and the
vendored package list are no longer this repository's to carry.
"""

import re
import tarfile


ARCH = "amd64"


def source_version(project, tarball):
    """What the tarball says it is.

    Read straight out of the archive rather than from an unpacked tree: the
    unpacking happens inside snapcraft's instance, and the point of this check
    is to fail before the compile rather than after it.
    """
    with tarfile.open(tarball) as tar:
        member = next((m for m in tar.getmembers()
                       if m.name.endswith("/CMakeLists.txt")
                       and m.name.count("/") == 1), None)
        if member is None:
            project.die(f"no top-level CMakeLists.txt in {tarball.name}: "
                        f"the tarball layout changed")
        text = tar.extractfile(member).read().decode()

    parts = [re.search(rf'^set\(TR_VERSION_{field} "([^"]*)"', text, re.M)
             for field in ("MAJOR", "MINOR", "PATCH")]
    if not all(parts):
        project.die("could not read TR_VERSION_* out of CMakeLists.txt")
    return ".".join(found.group(1) for found in parts)


def build(project):
    tarball = project.artifact("transmission-*.tar.xz")
    project.need_tools("snapcraft")

    # Before the compile, so a mismatch costs a second and not ten minutes.
    project.check_version(source_version(project, tarball), "the tarball")

    project.say(f"building Transmission {project.version}  (from {tarball.name})")

    # No clean first: craft-parts re-pulls when the source changes.
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")

    built = project.directory / f"transmission_{project.version}_{ARCH}.snap"
    if not built.is_file():
        project.die(f"build finished but {built.name} was not produced")
    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")

    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous {built.name}")
    return built
