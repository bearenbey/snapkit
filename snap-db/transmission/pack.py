"""Rebuild the Transmission snap from the official upstream source release."""

import re
import tarfile


def source_version(project, tarball):
    """What the tarball says it is."""
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

    # Before the compile, so a mismatch costs a second and not ten minutes.
    project.check_version(source_version(project, tarball), "the tarball")

    project.say(f"building Transmission {project.version}  (from {tarball.name})")
    return project.finish(project.pack())
