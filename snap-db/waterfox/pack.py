"""Rebuild the Waterfox snap from the .deb BrowserWorks publishes."""

import re

# Where the .deb puts the payload; the launcher and policies overlay spell it too.
APP = "usr/lib/waterfox"
# What application.ini must say: the dbus slot is derived from this name.
REMOTING_NAME = "waterfox"
# Waterfox numbers its releases (6.7.3) apart from the Gecko underneath
# (153.3.0). application.ini carries the Gecko one; the release is only in
# AppConstants, which omni.ja stores uncompressed, so it is read off the
# raw file rather than through a zip reader that omni.ja's odd header trips.
DISPLAY_VERSION = re.compile(rb'MOZ_APP_VERSION_DISPLAY:\s*"([^"]+)"')


def display_version(app):
    """The version Waterfox shows in its own about dialog, or None."""
    omni = app / "omni.ja"
    if not omni.is_file():
        return None
    found = DISPLAY_VERSION.search(omni.read_bytes())
    return found.group(1).decode("ascii", "replace") if found else None


def build(project):
    deb = project.artifact("waterfox_*_amd64.deb")
    # The file name says one version; the control stanza inside is the truth.
    project.check_version(project.deb_field(deb, "Version"),
                          f"{deb.name}'s control stanza")
    project.say(f"building Waterfox {project.version}  (from {deb.name})")

    built = project.pack()
    with project.unpacked(built) as root:
        app = root / APP
        if not (app / "waterfox").is_file():
            project.die(f"no {APP}/waterfox in the packed snap: "
                        f"the .deb layout changed")

        # Or the snap advertises a version its payload does not have. The
        # .deb is 6.7.3-0 and the payload calls itself 6.7.3: compare
        # without the Debian revision.
        reported = display_version(app)
        if reported is None:
            project.die(f"no MOZ_APP_VERSION_DISPLAY in {APP}/omni.ja: "
                        f"the payload layout changed")
        project.check_version(reported, "the packed payload",
                              expected=project.version.partition("-")[0])

        ini = project.application_ini(app)
        remoting = ini.get("App", "RemotingName", fallback=None)
        if remoting != REMOTING_NAME:
            project.die(f"application.ini says RemotingName={remoting}, but "
                        f"the dbus slot expects {REMOTING_NAME}")
        project.note(f"Gecko {ini.get('App', 'Version', fallback='?')}, "
                     f"remoting name {remoting}")

        project.warn_unprovided(app, app / "waterfox", app / "waterfox-bin")

    # browser-sandbox does not auto-connect for a local --dangerous install.
    return project.finish(built, "browser-sandbox", "u2f-devices")
