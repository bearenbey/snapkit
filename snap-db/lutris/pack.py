"""Rebuild the Lutris snap from the official upstream .deb."""

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


def build(project):
    project.say(f"building lutris {project.version}")
    built = project.pack()

    with project.unpacked(built) as root:
        if not (root / "usr/games/lutris").is_file():
            project.die("no usr/games/lutris in the packed snap: "
                        "the deb layout changed")

        # The .deb ships the application and nothing it imports, so the whole
        # of this list comes from stage-packages and none of it is checked
        # anywhere else. Without it lutris dies on `import gi` when first run.
        staged = project.python_modules(root)
        missing = [name for name in IMPORTS if name not in staged]
        if missing:
            project.die(f"the snap is missing python modules lutris imports: "
                        f"{', '.join(missing)} -- add them to stage-packages")

        absent = [tool for tool in TOOLS
                  if not any((root / d / tool).exists() for d in ("usr/bin", "bin"))]
        if absent:
            project.die(f"lutris shells out to these and they are not staged: "
                        f"{', '.join(absent)}")

        # This is what a strictly confined GtkApplication dies on: "not
        # allowed to own the service net.lutris.Lutris due to AppArmor policy".
        declared = (root / "meta/snap.yaml").read_text(encoding="utf-8")
        if BUS_NAME not in declared:
            project.die(f"the snap declares no dbus slot for {BUS_NAME}, so it "
                        f"will not be allowed to own its own application id")

        # Lutris shadows a great deal of the platform and works anyway,
        # because its GTK3 stack and the platform's are the same versions. A
        # warning rather than a refusal for that reason -- but it is the first
        # thing to look at if a platform update breaks the launch with an
        # undefined symbol, which is exactly how clamui failed.
        overlap = project.shadowing(root)
        if overlap:
            project.warn(f"{len(overlap)} staged libraries are also in the gnome "
                         f"platform snap and shadow it. Lutris runs regardless, "
                         f"because the versions match; if a platform update ever "
                         f"breaks the launch with an undefined symbol, start "
                         f"here: {', '.join(overlap[:6])} ...")

    project.note(VULKAN_NOTE)
    project.note("lutris downloads and runs its own wine builds, so the "
                 "first launch has nothing to play with until a runner is "
                 "installed from its own interface.")
    return project.finish(built)
