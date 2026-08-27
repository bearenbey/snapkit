"""Rebuild the Shotcut snap from the official upstream tarball."""

import pathlib
import shutil
import tempfile

# Upstream's own launcher, which its second line insists on: "Run this
# instead of trying to run bin/shotcut. It runs shotcut with the correct
# environment." It sets QT_PLUGIN_PATH and the MLT, movit, frei0r and LADSPA
# paths, and without it the editor aborts before it draws anything.
LAUNCHER = "shotcut"

# The Qt platform plugins the launcher has to be able to find. The session
# exports QT_QPA_PLATFORM=wayland-egl, a Qt5 name that matches neither.
PLATFORMS = ("libqwayland.so", "libqxcb.so")

# The framework that does the actual editing, bundled beside the binary.
BUNDLED = ("lib/libmlt-7.so.7", "lib/libQt6Core.so.6", "lib/qt6/platforms")

BUS_NAME = "org.shotcut.Shotcut"


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


def built_snap(project):
    """The snap snapcraft just produced, whatever architecture it is for."""
    made = sorted(project.directory.glob(f"shotcut_{project.version}_*.snap"))
    if not made:
        project.die(f"build finished but no shotcut_{project.version}_*.snap "
                    f"was produced")
    return made[-1]


def build(project):
    project.need_tools("snapcraft", "unsquashfs")
    project.say(f"building shotcut {project.version}")

    # No clean first: craft-parts re-pulls when the source changes.
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")
    built = built_snap(project)

    project.say("checking the packed snap")
    root = unpacked(project, built)
    declared = (root / "meta/snap.yaml").read_text(encoding="utf-8")

    # Running bin/shotcut instead of the launcher is the failure this check
    # exists for: no QT_PLUGIN_PATH, and it aborts on the platform plugin.
    if f"command: {LAUNCHER}\n" not in declared:
        refuse(project, built, root.parent,
               f"the snap does not run {LAUNCHER}, upstream's own launcher. "
               f"bin/shotcut starts with no QT_PLUGIN_PATH and aborts.")
    if not (root / LAUNCHER).is_file():
        refuse(project, built, root.parent,
               f"no {LAUNCHER} in the packed snap: the tarball layout changed")

    for relative in BUNDLED:
        if not (root / relative).exists():
            refuse(project, built, root.parent,
                   f"no {relative} in the packed snap: shotcut bundles its "
                   f"own Qt and MLT, and this one did not come with it")

    plugins = root / "lib/qt6/platforms"
    absent = [p for p in PLATFORMS if not (plugins / p).is_file()]
    if absent:
        refuse(project, built, root.parent,
               f"the bundled Qt has no {', '.join(absent)}, so there is no "
               f"platform left for it to start on")

    # The session's QT_QPA_PLATFORM is a Qt5 spelling the bundled Qt6 has no
    # plugin for, so the recipe has to override it rather than inherit it.
    if "QT_QPA_PLATFORM" not in declared:
        refuse(project, built, root.parent,
               "the snap does not set QT_QPA_PLATFORM, so it inherits "
               "wayland-egl from the session and finds no such plugin")

    if BUS_NAME not in declared:
        project.warn(f"no dbus slot for {BUS_NAME}: harmless unless shotcut "
                     f"starts claiming its application id")

    shutil.rmtree(root.parent, ignore_errors=True)
    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")
    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous {built.name}")
    project.note("upstream publish their own shotcut snap, and it is current. "
                 "This one exists to be built here, not to better it.")
    return built
