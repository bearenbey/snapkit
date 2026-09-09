"""Build the kernel-remover snap: test the script, pack it, check the result."""

import sys
from pathlib import Path

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


def check_payload(project, root):
    """Refuse a snap that does not carry exactly the files in snap/local."""
    for path, source in SHIPPED.items():
        packed = root / path
        if not packed.is_file():
            project.die(f"the packed snap has no {path}: the recipe's organize changed")
        if packed.read_bytes() != (project.directory / source).read_bytes():
            project.die(f"the packed snap ships a {path} that is not {source}")
    # Directories on the way to a shipped file are fine; anything else is not.
    parents = {str(p) for path in SHIPPED for p in Path(path).parents}
    stray = sorted(str(p.relative_to(root)) for top in ("bin", "lib")
                   if (root / top).is_dir() for p in (root / top).rglob("*")
                   if str(p.relative_to(root)) not in SHIPPED
                   and str(p.relative_to(root)) not in parents)
    if stray:
        project.die("the packed snap carries more than the script and its "
                    "launcher: " + ", ".join(stray))


def build(project):
    test(project)

    project.say(f"building kernel-remover {project.version}")
    built = project.pack(clean=True)
    with project.unpacked(built) as root:
        check_payload(project, root)

    # Classic, and unsigned: both need saying at install time.
    return project.finish(built)
