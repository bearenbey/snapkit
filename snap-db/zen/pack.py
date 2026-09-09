"""Rebuild the Zen Browser snap from the official upstream tarball."""

# Where the tarball's zen/ tree lands, per snap/snapcraft.yaml's organize.
APP = "opt/zen"


def build(project):
    tarball = project.artifact("zen.linux-x86_64.tar.xz")
    project.say(f"building Zen {project.version}  (from {tarball.name})")

    built = project.pack()
    with project.unpacked(built) as root:
        app = root / APP
        if not (app / "zen").is_file():
            project.die(f"no {APP}/zen in the packed snap: "
                        f"the tarball layout changed")

        # Or the snap advertises a version its payload does not have.
        reported = project.application_ini(app).get("App", "Version",
                                                    fallback=None)
        if reported is None:
            project.die(f"no [App] Version in {APP}/application.ini: "
                        f"the tarball layout changed")
        project.check_version(reported, "the packed payload")

        project.warn_unprovided(app, app / "zen")

    # browser-sandbox does not auto-connect for a local --dangerous install.
    return project.finish(built, "browser-sandbox", "u2f-devices")
