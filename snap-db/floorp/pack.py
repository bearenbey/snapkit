"""Rebuild the Floorp snap from the official upstream Linux tarball."""

# Where the tarball lands; the launcher and policies overlay spell it too.
APP = "usr/lib/floorp"


def build(project):
    tarball = project.artifact("floorp-linux-x86_64.tar.xz")
    project.say(f"building Floorp {project.version}  (from {tarball.name})")

    built = project.pack()
    with project.unpacked(built) as root:
        app = root / APP
        if not (app / "floorp").is_file():
            project.die(f"no {APP}/floorp in the packed snap: "
                        f"the tarball layout changed")

        # Or the snap advertises a version its payload does not have. Floorp
        # writes its own release and the Gecko under it as one field.
        ini = project.application_ini(app)
        version = ini.get("App", "Version", fallback=None)
        if version is None:
            project.die(f"no [App] Version in {APP}/application.ini: "
                        f"the tarball layout changed")
        reported, _, gecko = version.partition("@")
        project.check_version(reported, "the packed payload")
        project.note(f"Gecko {gecko or ini.get('Gecko', 'MinVersion', fallback='?')}")

        project.warn_unprovided(app, app / "floorp")

    # browser-sandbox does not auto-connect for a local --dangerous install.
    return project.finish(built, "browser-sandbox", "u2f-devices")
