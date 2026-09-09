"""Rebuild the Defold snap from the official upstream editor zip."""

# What libopenal may ask for; the base and platform snaps cover the rest.
EXPECTED_NEEDED = {
    "libsndio.so.7", "libstdc++.so.6", "libm.so.6",
    "libgcc_s.so.1", "libc.so.6", "ld-linux-x86-64.so.2",
    "libasound.so.2", "libpthread.so.0", "libdl.so.2", "librt.so.1",
}


def editor_version(root):
    """The version out of the editor's own launcher config."""
    config = root / "opt" / "Defold" / "config"
    if not config.is_file():
        return None
    for line in config.read_text().splitlines():
        if line.startswith("version ="):
            return line.split(" = ", 1)[1]
    return None


def check_openal(project, root):
    """Warn if libopenal grew a dependency nothing in the snap provides."""
    library = root / "usr/lib/x86_64-linux-gnu/libopenal.so.1"
    if not library.is_file():
        project.warn("no libopenal.so.1 in the packed snap; "
                     "the engine's audio will not load")
        return
    unexpected = sorted(set(project.needed(library)) - EXPECTED_NEEDED)
    if unexpected:
        project.warn(f"libopenal.so.1 now needs {' '.join(unexpected)}, which "
                     f"nothing in the snap or the platform snaps provides")


def build(project):
    zip_path = project.artifact("Defold-x86_64-linux.zip")
    project.say(f"building Defold {project.version}  (from {zip_path.name})")

    built = project.pack()
    with project.unpacked(built) as root:
        # Or the snap advertises a version its payload does not have.
        reported = editor_version(root)
        if reported is None:
            project.die("no version in opt/Defold/config: the zip layout changed")
        project.check_version(reported, "the packed editor")

        project.say("checking the engine's audio libraries")
        check_openal(project, root)

    return project.finish(built)
