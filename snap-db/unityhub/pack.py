"""Rebuild the Unity Hub snap from the official upstream .deb."""

# 3.20 moved the payload from /opt to /usr/lib; either is fine, neither is not.
BINARY = ("usr/lib/unityhub/unityhub-bin", "opt/unityhub/unityhub-bin")


def build(project):
    deb = project.artifact("UnityHubSetup-amd64.deb")

    # A warning: the .deb is packed either way, the metadata has to follow.
    reported = project.deb_field(deb, "Version")
    if reported != project.version:
        project.warn(f"{project.yaml} says {project.version}, but {deb.name} "
                     f"is {reported}\n         bump the version in "
                     f"{project.yaml.name} to match")

    project.say(f"building Unity Hub {project.version}  (from {deb.name})")

    built = project.pack()
    with project.unpacked(built) as root:
        binary = next((root / candidate for candidate in BINARY
                       if (root / candidate).is_file()), None)
        if binary is None:
            project.die("no unityhub-bin in the packed snap: "
                        "the payload layout changed")

        # Classic: these come from the host, and a gap only shows up at runtime.
        project.say("checking host libraries")
        project.warn_missing(binary,
                             "install them with: sudo apt install libgtk-3-0t64 "
                             "libnotify4 \\\n           libnss3 libxss1 libxtst6 "
                             "libatspi2.0-0t64 libsecret-1-0")

    return project.finish(built)
