"""Rebuild the Shotcut snap from the official upstream tarball."""

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


def build(project):
    project.say(f"building shotcut {project.version}")
    built = project.pack()

    with project.unpacked(built) as root:
        declared = (root / "meta/snap.yaml").read_text(encoding="utf-8")

        # Running bin/shotcut instead of the launcher is the failure this
        # check exists for: no QT_PLUGIN_PATH, and it aborts on the plugin.
        if f"command: {LAUNCHER}\n" not in declared:
            project.die(f"the snap does not run {LAUNCHER}, upstream's own "
                        f"launcher. bin/shotcut starts with no QT_PLUGIN_PATH "
                        f"and aborts.")
        if not (root / LAUNCHER).is_file():
            project.die(f"no {LAUNCHER} in the packed snap: "
                        f"the tarball layout changed")

        for relative in BUNDLED:
            if not (root / relative).exists():
                project.die(f"no {relative} in the packed snap: shotcut bundles "
                            f"its own Qt and MLT, and this one did not come "
                            f"with it")

        plugins = root / "lib/qt6/platforms"
        absent = [p for p in PLATFORMS if not (plugins / p).is_file()]
        if absent:
            project.die(f"the bundled Qt has no {', '.join(absent)}, so there "
                        f"is no platform left for it to start on")

        # The session's QT_QPA_PLATFORM is a Qt5 spelling the bundled Qt6 has
        # no plugin for, so the recipe has to override it rather than inherit.
        if "QT_QPA_PLATFORM" not in declared:
            project.die("the snap does not set QT_QPA_PLATFORM, so it inherits "
                        "wayland-egl from the session and finds no such plugin")

        if BUS_NAME not in declared:
            project.warn(f"no dbus slot for {BUS_NAME}: harmless unless shotcut "
                         f"starts claiming its application id")

    project.note("upstream publish their own shotcut snap, and it is current. "
                 "This one exists to be built here, not to better it.")
    return project.finish(built)
