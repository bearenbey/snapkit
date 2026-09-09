"""Rebuild the Godot snap from the official upstream editor zip."""


def build(project):
    zip_path = project.artifact("Godot_v*_linux.x86_64.zip")
    project.say(f"building Godot {project.version}  (from {zip_path.name})")

    built = project.pack()
    with project.unpacked(built) as root:
        editor = root / "bin" / "godot"
        if not editor.is_file():
            project.die(f"no bin/godot in the packed snap: {zip_path.name} holds "
                        f"no linux.x86_64 binary, or its name no longer matches "
                        f"the recipe's organize glob")

        # `--version` prints "<version>.stable.official.<hash>", no display needed.
        reported = project.capture(editor, "--version").split(".stable")[0].strip()
        project.check_version(reported, "the packed editor")

    return project.finish(built)
