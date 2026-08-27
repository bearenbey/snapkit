"""Rebuild the ClamUI snap from the official upstream .deb."""

import pathlib
import re
import shutil
import tempfile

# What the .deb carries and what it does not. ClamUI is `Architecture: all`
# python, so everything it imports is staged beside it, except the GI stack,
# which comes from the gnome platform snap on purpose. See shadowing() below.
IMPORTS = ("requests", "urllib3", "keyring", "matplotlib", "psutil", "PIL")

# The scanner it drives. Confined, there is no host clamav to reach.
TOOLS = ("clamscan", "freshclam")

BUS_NAME = "io.github.linx_systems.ClamUI"

# The platform carries PyGObject, so a second copy is two GI stacks for one
# process to disagree with.
NOT_OURS = ("gi", "cairo")


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

    This is the check the rest of the file exists for. The platform ships
    newer GNOME components than the base archive does, so a library staged
    from the archive loads first and the platform's own libraries then
    resolve against the older copy. It surfaces a long way from the cause:

        libharfbuzz-subset.so.0: undefined symbol: hb_free

    followed by Adw failing to import and Gdk's type registration returning
    TYPE_NONE. One library at a time was the wrong shape of fix; the answer
    is that the two trees must not overlap at all.
    """
    # lib*.so* only: perl and python extension modules are .so files too,
    # with names like Cwd.so and POSIX.so, and counting those buries the
    # real answer under hundreds of lines of noise.
    # Keyed on the architecture directory as well as the name, so a 32-bit
    # library is not counted as shadowing a 64-bit one of the same name.
    platform = project.gnome_platform() / "usr/lib"
    theirs = {(p.parent.name, p.name) for p in platform.rglob("lib*.so*")}
    ours = {(p.parent.name, p.name)
            for p in (root / "usr/lib").rglob("lib*.so*")}
    return sorted({re.match(r"(.+?)\.so", name).group(1)
                   for _arch, name in ours & theirs})


def built_snap(project):
    """The snap snapcraft just produced, whatever architecture it is for."""
    made = sorted(project.directory.glob(f"clamui_{project.version}_*.snap"))
    if not made:
        project.die(f"build finished but no clamui_{project.version}_*.snap "
                    f"was produced")
    return made[-1]


def build(project):
    project.need_tools("snapcraft", "unsquashfs")
    project.say(f"building clamui {project.version}")

    # No clean first: craft-parts re-pulls when the source changes.
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")
    built = built_snap(project)

    project.say("checking the packed snap")
    root = unpacked(project, built)

    entry = root / "usr/bin/clamui"
    if not entry.is_file():
        refuse(project, built, root.parent,
               "no usr/bin/clamui in the packed snap: the deb layout changed")

    staged = modules_in(root)
    missing = [name for name in IMPORTS if name not in staged]
    if missing:
        refuse(project, built, root.parent,
               f"the snap is missing python modules clamui imports: "
               f"{', '.join(missing)} -- add them to stage-packages")

    duplicated = [name for name in NOT_OURS if name in staged]
    if duplicated:
        refuse(project, built, root.parent,
               f"{', '.join(duplicated)} is staged here and the gnome "
               f"platform has it too: two GI stacks in one process is how "
               f"`from gi.repository import Adw` ends in an AssertionError")

    project.say("checking nothing shadows the gnome platform")
    overlap = shadowing(project, root)
    if overlap:
        refuse(project, built, root.parent,
               f"{len(overlap)} librar{'y' if len(overlap) == 1 else 'ies'} "
               f"staged here are also in the platform snap and will shadow "
               f"it: {', '.join(overlap[:8])}"
               f"{' ...' if len(overlap) > 8 else ''}\n"
               f"           add them to the part's `stage:` exclusions")

    absent = [tool for tool in TOOLS if not (root / "usr/bin" / tool).exists()]
    if absent:
        refuse(project, built, root.parent,
               f"no {', '.join(absent)} in the snap: confined, there is no "
               f"host clamav to fall back on")

    declared = (root / "meta/snap.yaml").read_text(encoding="utf-8")
    if BUS_NAME not in declared:
        refuse(project, built, root.parent,
               f"the snap declares no dbus slot for {BUS_NAME}, so it will "
               f"not be allowed to own its own application id")

    shutil.rmtree(root.parent, ignore_errors=True)
    project.say(f"built {built.name} ({built.stat().st_size / 1e6:.0f} MB)")
    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous {built.name}")
    project.note("system-wide ClamAV preferences need a pkexec helper on the "
                 "host, which no sandboxed install can place. Scanning, which "
                 "is the point, does not.")
    return built
