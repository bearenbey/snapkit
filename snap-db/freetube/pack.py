"""Rebuild the FreeTube snap from the official upstream .deb."""


def deb_contents(project, deb):
    """The absolute paths inside a .deb, as `dpkg-deb -c` reports them."""
    paths = set()
    for line in project.capture("dpkg-deb", "-c", deb).splitlines():
        fields = line.split()
        # perms owner size date time name [-> target]
        if len(fields) >= 6:
            paths.add(fields[5].lstrip("."))
    return paths


def build(project):
    deb = project.artifact("freetube_*_amd64.deb")
    project.need_tools("dpkg-deb")

    # Upstream tags the release beta; the deb inside carries the bare version.
    project.check_version(project.deb_field(deb, "Version"), deb.name,
                          expected=project.version.removesuffix("-beta"))

    # electron-builder moves this between /opt and /usr/lib; accept either.
    contents = deb_contents(project, deb)
    if not contents & {"/opt/FreeTube/freetube", "/usr/lib/freetube/freetube"}:
        project.die(f"no freetube binary in {deb.name}: the payload layout changed")

    project.say(f"building FreeTube {project.version}  (from {deb.name})")
    return project.finish(project.pack())
