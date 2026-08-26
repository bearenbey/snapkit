"""Rebuild the Neovim snap from the official upstream release tarball."""

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
    tarball = project.artifact("nvim-linux-x86_64.tar.gz")
    project.need_tools("snapcraft", "unsquashfs")

    project.say(f"building Neovim {project.version}  (from {tarball.name})")

    # No clean first: craft-parts re-pulls when the source changes.
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")

    built = project.directory / f"nvim_{project.version}_{ARCH}.snap"
    if not built.is_file():
        project.die(f"build finished but {built.name} was not produced")

    project.say("checking the packed snap")
    root = unpacked(project, built)
    nvim = root / "usr" / "bin" / "nvim"
    if not nvim.is_file():
        refuse(project, built, root.parent,
               "no usr/bin/nvim in the packed snap: the tarball layout changed")

    # Or the snap advertises a version its payload does not have.
    reported = project.capture(nvim, "--version").split("\n")[0].split()[1].lstrip("v")
    if reported != project.version:
        refuse(project, built, root.parent,
               f"version mismatch: snapcraft.yaml says {project.version}, "
               f"the packed binary reports {reported}")

    # Classic: these come from the host, and a gap only shows up at runtime.
    project.say("checking host libraries")
    project.warn_missing(nvim)

    # Parsers are dlopened, so a gap shows only when that filetype is opened.
    for parser in sorted((root / "usr/lib/nvim/parser").glob("*.so")):
        missing = project.missing_libraries(parser)
        if missing:
            project.warn(f"{parser.name} is missing: {' '.join(missing)}")

    shutil.rmtree(root.parent, ignore_errors=True)

    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")
    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous --classic {built.name}")
    return built
