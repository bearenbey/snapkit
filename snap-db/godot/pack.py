"""Rebuild the Godot snap from the official upstream editor zip."""

import pathlib
import shutil
import tempfile

ARCH = "amd64"


def unpacked(project, snap):
    """The packed snap's contents, extracted to a temporary directory."""
    out = pathlib.Path(tempfile.mkdtemp(prefix="snapkit-check-"))
    project.run("unsquashfs", "-q", "-d", out / "root", snap)
    return out / "root"


def refuse(project, snap, holding, message):
    """Delete a snap that failed its checks, then say why."""
    shutil.rmtree(holding, ignore_errors=True)
    snap.unlink(missing_ok=True)
    project.die(message)


def build(project):
    zip_path = project.artifact("Godot_v*_linux.x86_64.zip")
    project.need_tools("snapcraft", "unsquashfs")

    project.say(f"building Godot {project.version}  (from {zip_path.name})")

    # No clean first: craft-parts re-pulls when the source changes.
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")

    built = project.directory / f"godot_{project.version}_{ARCH}.snap"
    if not built.is_file():
        project.die(f"build finished but {built.name} was not produced")

    project.say("checking the packed snap")
    root = unpacked(project, built)
    editor = root / "bin" / "godot"
    if not editor.is_file():
        refuse(project, built, root.parent,
               f"no bin/godot in the packed snap: {zip_path.name} holds no "
               f"linux.x86_64 binary, or its name no longer matches the "
               f"recipe's organize glob")

    # `--version` prints "<version>.stable.official.<hash>", no display needed.
    reported = project.capture(editor, "--version").split(".stable")[0].strip()
    if reported != project.version:
        refuse(project, built, root.parent,
               f"version mismatch: snapcraft.yaml says {project.version}, "
               f"the packed editor reports {reported}")
    shutil.rmtree(root.parent, ignore_errors=True)

    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")
    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous {built.name}")
    return built
