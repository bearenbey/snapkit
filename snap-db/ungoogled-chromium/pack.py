"""Rebuild the ungoogled-chromium snap against an upstream portable release."""

import tarfile


def check_payload(project, tarball):
    """Refuse to pack a snap whose payload is not the release it claims."""
    with tarfile.open(tarball) as tar:
        first = tar.next()
    if first is None:
        project.die(f"{tarball.name} is empty")
    top = first.name.split("/")[0]
    expected = f"ungoogled-chromium-{project.version}-x86_64_linux"
    if top != expected:
        project.die(f"version mismatch: {project.snapcraft_yaml.name} says "
                    f"{project.version}, {tarball.name} contains {top}/")
    project.note(f"{tarball.name} contains {top}/")


def build(project):
    # The recipe's own source:, so a superseded tarball beside it is not built.
    tarball = project.artifact("ungoogled-chromium-*-x86_64_linux.tar.xz")
    project.say(f"building ungoogled-chromium {project.version}  "
                f"(from {tarball.name})")
    check_payload(project, tarball)

    # Stale parts leak into the pull step, and a browser is big enough to care.
    built = project.pack(clean=True)

    # Neither auto-connects, and without browser-sandbox it will not start.
    return project.finish(built, "browser-sandbox", "u2f-devices")
