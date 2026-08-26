"""Rebuild the FreeTube snap from the official upstream .deb.

`snapkit build freetube` calls build() below with a Build; `snapkit update
freetube` fetches the release it packs first.

This is a real snapcraft build -- snap/snapcraft.yaml is run as written -- so
what is left here is the work a recipe cannot express: refusing to pack a snap
whose payload is not the release the recipe claims, and catching the
electron-builder layout move that would otherwise produce a snap which packs
cleanly and then dies on exec.
"""

ARCH = "amd64"


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
    project.need_tools("dpkg-deb", "snapcraft")

    # Upstream tags the release beta; the deb inside carries the bare version.
    reported = project.capture("dpkg-deb", "-f", deb, "Version")
    if reported != project.version.removesuffix("-beta"):
        project.die(f"version mismatch: {project.yaml.name} says {project.version}, "
                    f"the deb ships {reported}")

    # electron-builder moves this between /opt and /usr/lib; accept either.
    contents = deb_contents(project, deb)
    if not contents & {"/opt/FreeTube/freetube", "/usr/lib/freetube/freetube"}:
        project.die(f"no freetube binary in {deb.name}: the payload layout changed")

    project.say(f"building FreeTube {project.version}  (from {deb.name})")

    # No clean first: craft-parts re-pulls when the source changes.
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")

    built = project.directory / f"freetube_{project.version}_{ARCH}.snap"
    if not built.is_file():
        project.die(f"build finished but {built.name} was not produced")
    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")

    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous {built.name}")
    return built
