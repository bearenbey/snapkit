"""Rebuild the Firefox ESR snap from the official upstream Linux tarball."""

import re

# Where the tarball lands; the launcher and policies overlay spell it too.
APP = "usr/lib/firefox-esr"
# What application.ini must say: the D-Bus slot and the desktop entry's
# StartupWMClass are both derived from this name.
REMOTING_NAME = "firefox-esr"


def update_channel(app):
    """The channel out of channel-prefs.js: "esr", "release", or None."""
    path = app / "defaults" / "pref" / "channel-prefs.js"
    if not path.is_file():
        return None
    found = re.search(r'pref\("app\.update\.channel",\s*"([^"]*)"\)',
                      path.read_text(errors="replace"))
    return found.group(1) if found else None


def build(project):
    tarball = project.artifact("firefox-*esr.tar.xz")
    project.say(f"building Firefox ESR {project.version}  (from {tarball.name})")

    built = project.pack()
    with project.unpacked(built) as root:
        app = root / APP
        if not (app / "firefox").is_file():
            project.die(f"no {APP}/firefox in the packed snap: "
                        f"the tarball layout changed")

        # Or the snap advertises a version its payload does not have. The
        # tarball is firefox-140.15.0esr.tar.xz; application.ini says 140.15.0.
        ini = project.application_ini(app)
        reported = ini.get("App", "Version", fallback=None)
        if reported is None:
            project.die(f"no [App] Version in {APP}/application.ini: "
                        f"the tarball layout changed")
        project.check_version(reported, "the packed payload",
                              expected=project.version.removesuffix("esr"))

        # A mainline Firefox tarball unpacks to the same layout and would pack
        # cleanly; only the channel and the remoting name tell them apart.
        channel = update_channel(app)
        if channel != "esr":
            project.die(f"the payload is on the {channel or 'unknown'} channel, "
                        f"not esr: this is not an ESR tarball")
        remoting = ini.get("App", "RemotingName", fallback=None)
        if remoting != REMOTING_NAME:
            project.die(f"application.ini says RemotingName={remoting}, but the "
                        f"dbus slot and desktop entry expect {REMOTING_NAME}")
        project.note(f"channel {channel}, remoting name {remoting}")

        project.warn_unprovided(app, app / "firefox")

    # browser-sandbox does not auto-connect for a local --dangerous install.
    return project.finish(built, "browser-sandbox", "u2f-devices")
