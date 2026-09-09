"""Rebuild the ClamUI snap from the official upstream .deb."""

# What the .deb carries and what it does not. ClamUI is `Architecture: all`
# python, so everything it imports is staged beside it, except the GI stack,
# which comes from the gnome platform snap on purpose.
IMPORTS = ("requests", "urllib3", "keyring", "matplotlib", "psutil", "PIL")

# The scanner it drives. Confined, there is no host clamav to reach.
TOOLS = ("clamscan", "freshclam")

BUS_NAME = "io.github.linx_systems.ClamUI"

# The platform carries PyGObject, so a second copy is two GI stacks for one
# process to disagree with.
NOT_OURS = ("gi", "cairo")


def build(project):
    project.say(f"building clamui {project.version}")
    built = project.pack()

    with project.unpacked(built) as root:
        if not (root / "usr/bin/clamui").is_file():
            project.die("no usr/bin/clamui in the packed snap: "
                        "the deb layout changed")

        staged = project.python_modules(root)
        missing = [name for name in IMPORTS if name not in staged]
        if missing:
            project.die(f"the snap is missing python modules clamui imports: "
                        f"{', '.join(missing)} -- add them to stage-packages")
        duplicated = [name for name in NOT_OURS if name in staged]
        if duplicated:
            project.die(f"{', '.join(duplicated)} is staged here and the gnome "
                        f"platform has it too: two GI stacks in one process is "
                        f"how `from gi.repository import Adw` ends in an "
                        f"AssertionError")

        # This is the check the rest of the file exists for. A library staged
        # from the archive loads before the platform's newer copy, and it
        # surfaces a long way from the cause:
        #
        #     libharfbuzz-subset.so.0: undefined symbol: hb_free
        #
        # followed by Adw failing to import and Gdk's type registration
        # returning TYPE_NONE. One library at a time was the wrong shape of
        # fix; the answer is that the two trees must not overlap at all.
        project.say("checking nothing shadows the gnome platform")
        overlap = project.shadowing(root)
        if overlap:
            project.die(f"{len(overlap)} librar{'y' if len(overlap) == 1 else 'ies'} "
                        f"staged here are also in the platform snap and will "
                        f"shadow it: {', '.join(overlap[:8])}"
                        f"{' ...' if len(overlap) > 8 else ''}\n"
                        f"           add them to the part's `stage:` exclusions")

        absent = [tool for tool in TOOLS
                  if not (root / "usr/bin" / tool).exists()]
        if absent:
            project.die(f"no {', '.join(absent)} in the snap: confined, there "
                        f"is no host clamav to fall back on")

        declared = (root / "meta/snap.yaml").read_text(encoding="utf-8")
        if BUS_NAME not in declared:
            project.die(f"the snap declares no dbus slot for {BUS_NAME}, so it "
                        f"will not be allowed to own its own application id")

    project.note("system-wide ClamAV preferences need a pkexec helper on the "
                 "host, which no sandboxed install can place. Scanning, which "
                 "is the point, does not.")
    return project.finish(built)
