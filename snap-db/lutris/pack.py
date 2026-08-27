"""Rebuild the Lutris snap from the official upstream .deb."""

import pathlib
import re
import shutil
import tempfile

# What the .deb carries and what it does not. Lutris is `Architecture: all`
# python, so everything it imports is staged beside it and none of it is
# checked by snapcraft: a missing name is an ImportError at first launch.
IMPORTS = ("yaml", "lxml", "requests", "PIL", "gi", "dbus", "distro",
           "magic", "setproctitle", "google", "evdev", "pefile")

# It shells out to these while installing and running a game.
TOOLS = ("lspci", "cabextract", "unzip", "curl", "xrandr", "killall",
         "vulkaninfo", "mangohud", "winetricks")

# The diagnostics row is LinuxSystem.is_vulkan_supported, which is stricter
# than the vkquery function of the same name: it also wants libvulkan.so.1
# for *both* architectures, read out of `ldconfig -p`. A snap inherits the
# host's /etc/ld.so.cache verbatim, so the i386 half is answered by a package
# on the host and by nothing in this recipe. Everything needed to actually
# run 32-bit vulkan is already in the snap -- the i386 loader and mesa
# drivers in gpu-2404, and the 32-bit nvidia driver snap-confine bridges into
# /var/lib/snapd/lib/gl32.
VULKAN_NOTE = ("if lutris reports \"Vulkan support: NO\", it wants a 32-bit "
               "loader: `sudo apt install libvulkan1:i386` on the HOST. The "
               "check reads the host's ld.so.cache, which a snap inherits "
               "whole, while the 32-bit runtime it then uses is the snap's "
               "own from gpu-2404. Nothing to change in this recipe.")

# A GtkApplication registers its id on the session bus, and confinement
# refuses a name the snap has not declared a slot for.
BUS_NAME = "net.lutris.Lutris"


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


def modules_in(root):
    """The python packages staged alongside the application."""
    found = set()
    for where in root.glob("usr/lib/python3*/dist-packages"):
        found |= {p.name.split(".")[0] for p in where.iterdir()}
    return found


def shadowing(project, root):
    """Libraries staged here that the gnome platform snap also provides.

    Lutris shadows a great deal of the platform and works anyway, because
    its GTK3 stack and the platform's are the same versions. This is a
    warning rather than a refusal for that reason -- but it is the first
    thing to look at if a future platform update breaks the launch with an
    undefined symbol, which is exactly how clamui failed.
    """
    # lib*.so* only: perl and python extension modules are .so files too,
    # with names like Cwd.so and POSIX.so, and counting those buried the
    # real answer under three hundred lines of noise.
    # Keyed on the architecture directory as well as the name: the snap
    # carries a 32-bit tree now, and an i386 libFLAC.so does not shadow the
    # platform's amd64 one -- they are different files for different
    # processes. Comparing bare names called all 360 of them a conflict.
    platform = project.gnome_platform() / "usr/lib"
    theirs = {(p.parent.name, p.name) for p in platform.rglob("lib*.so*")}
    ours = {(p.parent.name, p.name)
            for p in (root / "usr/lib").rglob("lib*.so*")}
    return sorted({re.match(r"(.+?)\.so", name).group(1)
                   for _arch, name in ours & theirs})


def built_snap(project):
    """The snap snapcraft just produced, whatever architecture it is for."""
    made = sorted(project.directory.glob(f"lutris_{project.version}_*.snap"))
    if not made:
        project.die(f"build finished but no lutris_{project.version}_*.snap "
                    f"was produced")
    return made[-1]


def build(project):
    project.need_tools("snapcraft", "unsquashfs")
    project.say(f"building lutris {project.version}")

    # No clean first: craft-parts re-pulls when the source changes.
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")
    built = built_snap(project)

    project.say("checking the packed snap")
    root = unpacked(project, built)

    entry = root / "usr/games/lutris"
    if not entry.is_file():
        refuse(project, built, root.parent,
               "no usr/games/lutris in the packed snap: the deb layout changed")

    # The .deb ships the application and nothing it imports, so the whole of
    # this list comes from stage-packages and none of it is checked anywhere
    # else. Without it lutris dies on `import gi` the first time it is run.
    missing = [name for name in IMPORTS if name not in modules_in(root)]
    if missing:
        refuse(project, built, root.parent,
               f"the snap is missing python modules lutris imports: "
               f"{', '.join(missing)} -- add them to stage-packages")

    absent = [tool for tool in TOOLS
              if not any((root / d / tool).exists() for d in ("usr/bin", "bin"))]
    if absent:
        refuse(project, built, root.parent,
               f"lutris shells out to these and they are not staged: "
               f"{', '.join(absent)}")

    # This is what a strictly confined GtkApplication dies on: "not allowed
    # to own the service net.lutris.Lutris due to AppArmor policy".
    declared = (root / "meta/snap.yaml").read_text(encoding="utf-8")
    if BUS_NAME not in declared:
        refuse(project, built, root.parent,
               f"the snap declares no dbus slot for {BUS_NAME}, so it will "
               f"not be allowed to own its own application id")

    overlap = shadowing(project, root)
    if overlap:
        project.warn(f"{len(overlap)} staged libraries are also in the gnome "
                     f"platform snap and shadow it. Lutris runs regardless, "
                     f"because the versions match; if a platform update ever "
                     f"breaks the launch with an undefined symbol, start "
                     f"here: {', '.join(overlap[:6])} ...")

    shutil.rmtree(root.parent, ignore_errors=True)
    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")
    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous {built.name}")
    project.note(VULKAN_NOTE)
    project.note("lutris downloads and runs its own wine builds, so the "
                 "first launch has nothing to play with until a runner is "
                 "installed from its own interface.")
    return built
