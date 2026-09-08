"""Build the kernel-remover snap: test the script, pack it, check the result."""

import sys
from pathlib import Path

ARCH = "amd64"
LOCAL = Path("snap/local")
# The whole payload, and where each file comes from. Anything else under
# bin/ or lib/ is a recipe mistake.
SHIPPED = {
    "bin/kernel-remover": LOCAL / "kernel-remover-launch",
    "lib/kernel-remover/kernel_remover.py": LOCAL / "kernel_remover.py",
}


def test(project):
    """The planner's own tests. They touch no apt, dpkg or /boot."""
    project.say("running the script's tests")
    project.run(sys.executable, project.directory / LOCAL / "tests.py")


def check_payload(project, built):
    """Refuse a snap that does not carry exactly the files in snap/local."""
    listing = project.capture("unsquashfs", "-l", built)
    inside = {line[len("squashfs-root/"):]
              for line in listing.splitlines() if line.startswith("squashfs-root/")}
    for path, source in SHIPPED.items():
        if path not in inside:
            project.die(f"{built.name} has no {path}: the recipe's organize changed")
        packed = project.run("unsquashfs", "-cat", built, path, capture_output=True).stdout
        if packed != (project.directory / source).read_bytes():
            project.die(f"{built.name} ships a {path} that is not {source}")
    # Directories on the way to a shipped file are fine; anything else is not.
    parents = {str(p) for path in SHIPPED for p in Path(path).parents}
    stray = sorted(p for p in inside
                   if p.split("/")[0] in ("bin", "lib") and p not in SHIPPED and p not in parents)
    if stray:
        project.die(f"{built.name} carries more than the script and its launcher: "
                    + ", ".join(stray))


def build(project):
    project.need_tools("snapcraft", "unsquashfs")
    test(project)

    project.say(f"building kernel-remover {project.version}")
    project.say("snapcraft clean")
    project.run("snapcraft", "clean")
    project.say("snapcraft pack")
    project.run("snapcraft", "pack")

    built = project.directory / f"kernel-remover_{project.version}_{ARCH}.snap"
    if not built.is_file():
        project.die(f"build finished but {built.name} was not produced")
    check_payload(project, built)
    project.say(f"built {built.name} ({built.stat().st_size / 1e3:.0f} kB)")

    # Classic, and unsigned: both need saying at install time.
    project.note(f"install it with:\n"
                 f"      sudo snap install --dangerous --classic {built.name}")
    return built
