"""Rebuild the LibreWolf snap from the official upstream Linux tarball."""

import json

# Where the tarball lands; the launcher spells it too.
APP = "usr/lib/librewolf"
# What application.ini must say: the dbus slot and the desktop entry's
# StartupWMClass are both derived from this name.
REMOTING_NAME = "librewolf"


def update_disabled(app):
    """Whether the payload's own policies.json turns the updater off.

    Unlike its Gecko siblings this snap ships no policy overlay, because
    LibreWolf's distribution/policies.json is its whole configuration and an
    overlay would replace it. So the one policy the snap relies on is read
    back out of upstream's file rather than assumed.
    """
    path = app / "distribution" / "policies.json"
    if not path.is_file():
        return None
    try:
        policies = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return policies.get("policies", {}).get("DisableAppUpdate") is True


def build(project):
    tarball = project.artifact("librewolf-*-linux-x86_64-package.tar.xz")
    project.say(f"building LibreWolf {project.version}  (from {tarball.name})")

    built = project.pack()
    with project.unpacked(built) as root:
        app = root / APP
        if not (app / "librewolf").is_file():
            project.die(f"no {APP}/librewolf in the packed snap: "
                        f"the tarball layout changed")

        # Or the snap advertises a version its payload does not have. The
        # release is 156.0-1, Firefox plus a packaging revision, and
        # application.ini carries only the Firefox half: compare without it.
        ini = project.application_ini(app)
        reported = ini.get("App", "Version", fallback=None)
        if reported is None:
            project.die(f"no [App] Version in {APP}/application.ini: "
                        f"the tarball layout changed")
        project.check_version(reported, "the packed payload",
                              expected=project.version.partition("-")[0])

        remoting = ini.get("App", "RemotingName", fallback=None)
        if remoting != REMOTING_NAME:
            project.die(f"application.ini says RemotingName={remoting}, but the "
                        f"dbus slot and desktop entry expect {REMOTING_NAME}")

        # The updater cannot write a squashfs, and the recipe has no policy
        # of its own to say so; upstream's has to, or the offer comes back.
        disabled = update_disabled(app)
        if disabled is None:
            project.die(f"no readable {APP}/distribution/policies.json: "
                        f"the snap relies on upstream's DisableAppUpdate")
        if not disabled:
            project.die(f"{APP}/distribution/policies.json no longer sets "
                        f"DisableAppUpdate: the snap needs a policy overlay now")
        project.note(f"remoting name {remoting}, updater disabled by "
                     f"upstream's policies.json")

        project.warn_unprovided(app, app / "librewolf", app / "librewolf-bin")

    # browser-sandbox does not auto-connect for a local --dangerous install.
    return project.finish(built, "browser-sandbox", "u2f-devices")
