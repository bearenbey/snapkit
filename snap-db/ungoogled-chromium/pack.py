"""Rebuild the ungoogled-chromium snap against an upstream portable release."""

import re
import tarfile

ARCH = "amd64"
TARBALL = "ungoogled-chromium-*-x86_64_linux.tar.xz"


def packaged_tarball(project):
    """The tarball the recipe names, which is the one to build."""
    for line in project.snapcraft_yaml.read_text().splitlines():
        found = re.match(r"^\s*source:\s*(ungoogled-chromium-.*\.tar\.xz)\s*$", line)
        if found:
            return project.directory / found.group(1)
    project.die(f"could not read the tarball source: from {project.snapcraft_yaml}")


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
    project.need_tools("snapcraft")

    tarball = packaged_tarball(project)
    if not tarball.is_file():
        project.die(f"{project.snapcraft_yaml.name} wants {tarball.name}, which "
                    f"is not here -- snapkit update ungoogled-chromium --force "
                    f"fetches it")

    project.say(f"building ungoogled-chromium {project.version}  "
                f"(from {tarball.name})")
    check_payload(project, tarball)

    # Stale parts leak into the pull step, and a browser is big enough to care.
    project.say("snapcraft clean")
    project.run("snapcraft", "clean")
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")

    built = project.directory / f"ungoogled-chromium_{project.version}_{ARCH}.snap"
    if not built.is_file():
        project.die(f"build finished but {built.name} was not produced")
    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")

    # Neither auto-connects, and without browser-sandbox it will not start.
    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous {built.name}\n"
                 f"      sudo snap connect ungoogled-chromium:browser-sandbox\n"
                 f"      sudo snap connect ungoogled-chromium:u2f-devices")
    return built
