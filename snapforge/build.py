"""The Build a project's pack.py is handed, and running the pack.py itself."""

import configparser
import contextlib
import fnmatch
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import depends, elf, platform, recipe
from .inspect import control_fields, missing_libraries
from .report import PlainReporter
from .versions import yaml_field, yaml_version

# The oldest snapkit whose Build a pack.py written against this one runs on.
# A project with a pack.py is published with this as what it needs, and a
# snapkit behind it refuses the project at pull time, by name, rather than
# failing on the first missing helper at build time. Raise it whenever a
# method is added to Build or one changes what it does.
NEEDS = "0.3.0"

# Where the desktop builds get their GTK and font helpers from.
GNOME_SNAP = Path("/snap/gnome-46-2404/current")

# What a strict snap under the gnome extension links against, when it is
# installed here: the base, the extension's platform snap, and mesa.
PLATFORM_SNAPS = (Path("/snap/core24/current"), GNOME_SNAP,
                  Path("/snap/mesa-2404/current"))

def file_source_parts(yaml_path):
    """The parts fed from a file here, as (part, file) pairs."""
    yaml_path = Path(yaml_path)
    if not yaml_path.is_file():
        return []
    directory = yaml_path.parent.parent
    text = yaml_path.read_text(encoding="utf-8", errors="replace")
    return [(part, directory / value) for part, value in recipe.sources(text)
            if (directory / value).is_file()]


def stale_parts(directory):
    """The parts whose file was replaced since the last snap was packed."""
    directory = Path(directory)
    packed = [path.stat().st_mtime for path in directory.glob("*.snap")]
    if not packed:
        return []
    return [name for name, source
            in file_source_parts(directory / "snap" / "snapcraft.yaml")
            if source.stat().st_mtime > max(packed)]


class BuildError(Exception):
    """Something the build cannot go on without."""


def die(text):
    raise BuildError(text)


def stream(command, reporter=None, **kwargs):
    """Run a command, handing each line it writes to the reporter."""
    if not getattr(reporter, "captures_output", False):
        return subprocess.run(command, **kwargs)

    kwargs.pop("capture_output", None)
    # Bytes, not text: universal newlines would split every \r redraw apart.
    with subprocess.Popen(command, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, **kwargs) as process:
        try:
            for raw in process.stdout:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                reporter.output(line.rsplit("\r", 1)[-1])
        except BaseException:
            # Popen's cleanup only waits, which is not stopping a long build.
            process.kill()
            raise
    return subprocess.CompletedProcess(command, process.returncode)


class Build:
    """One project's build, run from the project directory."""

    def __init__(self, app, directory, reporter=None, snapcraft_flags=()):
        self.app = app
        self.directory = Path(directory).resolve()
        os.chdir(self.directory)
        # Through here, so a dashboard run never writes over its own screen.
        self.reporter = reporter or PlainReporter()
        # Added to any snapcraft this build runs, --destructive-mode included.
        self.snapcraft_flags = list(snapcraft_flags)

    # -- saying things: on the Build, so a pack.py needs no import ----------

    def say(self, text):
        self.reporter.step(text)

    def note(self, text):
        self.reporter.detail(text)

    def warn(self, text):
        self.reporter.warn(text)

    die = staticmethod(die)

    # -- the packaging's own record of what is being built -------------------

    @property
    def meta_yaml(self):
        return self.directory / "overlay" / "meta" / "snap.yaml"

    @property
    def snapcraft_yaml(self):
        return self.directory / "snap" / "snapcraft.yaml"

    @property
    def yaml(self):
        """Whichever of the two carries this project's metadata."""
        return self.meta_yaml if self.meta_yaml.is_file() else self.snapcraft_yaml

    @property
    def version(self):
        """The version the packaging currently spells out."""
        version = yaml_version(self.yaml)
        if not version:
            die(f"could not read version: from {self.yaml}")
        return version

    @property
    def classic(self):
        """Whether the recipe asks for classic confinement."""
        return yaml_field(self.yaml, "confinement") == "classic"

    def recipe_source(self, pattern):
        """The file a part's `source:` names, if one matches the glob."""
        if not self.snapcraft_yaml.is_file():
            return None
        text = self.snapcraft_yaml.read_text(encoding="utf-8", errors="replace")
        return next((self.directory / value for _, value in recipe.sources(text)
                     if fnmatch.fnmatch(value, pattern)), None)

    # -- pre-flight ----------------------------------------------------------

    def need_tools(self, *names):
        for name in names:
            if not shutil.which(name):
                die(f"missing required tool: {name}")

    def need_file(self, path, hint=""):
        path = Path(path)
        if not path.is_file():
            die(f"no such file: {path}" + (f" -- {hint}" if hint else ""))
        return path

    def artifact(self, pattern, given=None):
        """The upstream file this build consumes.

        The recipe's own `source:` settles it where one matches: a project
        that keeps a superseded release beside the current one still builds
        the one the recipe names. Otherwise the glob has to be unambiguous.
        """
        if given:
            return self.need_file(given)
        named = self.recipe_source(pattern)
        if named is not None:
            return self.need_file(
                named, f"snapkit update {self.app} --force fetches it")
        found = sorted(self.directory.glob(pattern))
        if not found:
            die(f"no {pattern} in {self.directory.name} -- "
                f"snapkit update {self.app} --force fetches it")
        if len(found) > 1:
            die("more than one candidate here (" +
                ", ".join(one.name for one in found) +
                "); remove the superseded one")
        return found[0]

    def gnome_platform(self):
        if not GNOME_SNAP.is_dir():
            die(f"{GNOME_SNAP.parent.name} is not installed: "
                f"sudo snap install {GNOME_SNAP.parent.name}")
        return GNOME_SNAP

    def check_version(self, found, what, expected=None):
        """Refuse to pack a snap whose payload is not the version it claims."""
        expected = expected or self.version
        if found != expected:
            die(f"version mismatch: {self.yaml.name} says {expected}, "
                f"{what} ships {found}")
        return found

    # -- the packed snap: what every pack.py does around snapcraft -----------

    def pack(self, clean=False):
        """Run snapcraft, and hand back the .snap it made for this version."""
        self.need_tools("snapcraft")
        if clean:
            self.say("snapcraft clean")
            self.run("snapcraft", "clean")
        self.say("snapcraft pack")
        self.run("snapcraft", "pack")
        return self.packed()

    def packed(self):
        """The snap on disk for the recipe's version, whatever its arch."""
        made = sorted(self.directory.glob(f"{self.app}_{self.version}_*.snap"),
                      key=lambda path: path.stat().st_mtime)
        if not made:
            die(f"build finished but no {self.app}_{self.version}_*.snap "
                f"was produced")
        return made[-1]

    @contextlib.contextmanager
    def unpacked(self, snap):
        """The packed snap's contents, in a directory gone by the end.

        A check that dies inside the block takes the snap with it, so a
        payload that failed is not left where the next install finds it.
        """
        self.need_tools("unsquashfs")
        snap = Path(snap)
        holding = Path(tempfile.mkdtemp(prefix="snapkit-check-"))
        try:
            self.say("checking the packed snap")
            self.run("unsquashfs", "-n", "-q", "-d", holding / "root", snap)
            yield holding / "root"
        except BuildError:
            snap.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(holding, ignore_errors=True)

    def finish(self, built, *connect):
        """Say how to install what was built, and hand it back."""
        install = "sudo snap install --dangerous"
        if self.classic:
            install += " --classic"
        lines = [f"{install} {Path(built).name}"]
        # Named because none of these auto-connect for a --dangerous install.
        lines += [f"sudo snap connect {self.app}:{plug}" for plug in connect]
        self.note("install it with:\n" +
                  "\n".join(f"      {line}" for line in lines))
        return built

    # -- reading a payload: what more than one pack.py has to know -----------

    def application_ini(self, app):
        """A Gecko payload's application.ini, empty when there is none."""
        ini = configparser.ConfigParser(interpolation=None)
        ini.read(Path(app) / "application.ini", encoding="utf-8")
        return ini

    def deb_field(self, deb, name):
        """One field of a .deb's control stanza, or "" when it has none."""
        return control_fields(Path(deb)).get(name, "")

    def needed(self, binary):
        """The sonames a binary names in its ELF header, none resolved."""
        try:
            return elf.needed(binary)
        except elf.NotAnELF as exc:
            die(f"{Path(binary).name} is not an ELF binary: {exc}")

    def python_modules(self, root):
        """The python packages staged into a snap, by import name."""
        found = set()
        for where in Path(root).glob("usr/lib/python3*/dist-packages"):
            found |= {p.name.split(".")[0] for p in where.iterdir()}
        return found

    def shadowing(self, root):
        """Libraries staged here that the gnome platform snap also carries.

        The platform ships newer GNOME components than the base archive,
        so a library staged from the archive loads first and the platform's
        own then resolve against the older copy. It surfaces a long way from
        the cause, as an undefined symbol in a library nobody staged.
        """
        # lib*.so* only: perl and python extension modules are .so files
        # too, and counting those buries the answer. Keyed on the
        # architecture directory as well, so a 32-bit library is not
        # counted as shadowing a 64-bit one of the same name.
        theirs = {(p.parent.name, p.name) for p in
                  (self.gnome_platform() / "usr/lib").rglob("lib*.so*")}
        ours = {(p.parent.name, p.name)
                for p in (Path(root) / "usr/lib").rglob("lib*.so*")}
        return sorted({re.match(r"(.+?)\.so", name).group(1)
                       for _arch, name in ours & theirs})

    def unprovided(self, tree, *binaries):
        """What a strict payload links that neither it nor the platform has.

        {soname: [what asks for it]}, read out of the ELF headers of every
        library under `tree` and the binaries named. Driver libraries
        arrive through an interface and are not counted.
        """
        tree = Path(tree)
        bundled = depends.bundled_libraries(tree)
        available = depends.supplied(gui=True) | platform.FROM_THE_HOST
        for root in PLATFORM_SNAPS:
            if root.is_dir():
                available |= {path.name for path in root.rglob("*.so*")}
        missing = {}
        candidates = sorted(tree.rglob("*.so*")) + [Path(b) for b in binaries]
        for candidate in candidates:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            try:
                names = elf.needed(candidate)
            except elf.NotAnELF:
                continue
            for soname in names:
                if soname not in bundled and soname not in available:
                    missing.setdefault(soname, []).append(candidate.name)
        return missing

    def warn_unprovided(self, tree, *binaries):
        """Say what a strict snap's payload will not find at runtime."""
        self.say("checking platform libraries")
        missing = self.unprovided(tree, *binaries)
        for soname, users in sorted(missing.items()):
            self.warn(f"{soname} is in neither the base nor the platform "
                      f"snaps (needed by {', '.join(sorted(set(users)))})")
        return missing

    # -- running things ------------------------------------------------------

    def run(self, *command, **kwargs):
        kwargs.setdefault("check", True)
        kwargs.setdefault("cwd", self.directory)
        argv = [str(c) for c in command]
        if argv[:1] == ["snapcraft"]:
            argv += [flag for flag in self.snapcraft_flags if flag not in argv]

        # A caller that asked for the output wants it back, not reported.
        wants_output = any(k in kwargs for k in
                           ("capture_output", "stdout", "stderr", "input"))
        if wants_output or not self.reporter.captures_output:
            return subprocess.run(argv, **kwargs)

        check = kwargs.pop("check")
        done = stream(argv, self.reporter, **kwargs)
        if check and done.returncode != 0:
            raise subprocess.CalledProcessError(done.returncode, argv)
        return done

    def capture(self, *command, **kwargs):
        done = self.run(*command, capture_output=True, text=True, **kwargs)
        return done.stdout.strip()

    # -- files ---------------------------------------------------------------

    def copy(self, source, destination, executable=False):
        """Copy one file, making its parent if needed."""
        source, destination = Path(source), Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if executable:
            destination.chmod(destination.stat().st_mode | 0o111)
        return destination

    def missing_libraries(self, binary, root=None):
        """Whatever ldd cannot resolve for a binary, empty when it can."""
        return missing_libraries(binary, root)

    def warn_missing(self, binary, hint=""):
        """Say what a classic snap will not find on this host."""
        missing = self.missing_libraries(binary)
        if missing:
            self.warn("these libraries are missing on this host:\n"
                      + "\n".join(f"           {name}" for name in missing)
                      + (f"\n         {hint}" if hint else ""))
        return missing


# -- finding and running a project's pack.py ----------------------------------

def pack_module(directory, filename="pack.py"):
    """Import a project's pack.py, without it being on the path."""
    root = Path(directory).resolve()
    path = (root / filename).resolve()
    # This name can come off a fetched record, and importing it runs it.
    if root not in path.parents:
        die(f"{filename} is outside {root.name}, so it is not this project's")
    if not path.is_file():
        die(f"no {filename} in {root.name}")
    spec = importlib.util.spec_from_file_location(
        f"snapforge._pack.{root.name}", path)
    module = importlib.util.module_from_spec(spec)
    # Registered before running, so a pack.py can import from beside itself.
    sys.modules[spec.name] = module
    sys.path.insert(0, str(root))
    # A build should leave no __pycache__ behind in somebody's project.
    was_writing = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # Half-run, it must not stand in for the next import of that name.
        sys.modules.pop(spec.name, None)
        raise
    finally:
        sys.dont_write_bytecode = was_writing
        if sys.path and sys.path[0] == str(root):
            sys.path.pop(0)
    return module


def run_pack(app, directory, filename="pack.py", reporter=None,
             snapcraft_flags=()):
    """Assemble and pack one project. Returns the .snap it produced."""
    here = Path.cwd()
    try:
        module = pack_module(directory, filename)
        if not hasattr(module, "build"):
            die(f"{Path(directory) / filename} defines no build(project) function")
        return module.build(Build(app, directory, reporter, snapcraft_flags))
    finally:
        os.chdir(here)


SNAPCRAFT_LOGS = Path.home() / ".local/state/snapcraft/log"

# An interrupted run leaves a container LXD will not re-attach craft-state to.
STALE_INSTANCE = "Failed to add disk to instance"


# snapcraft's log carries every line it printed, behind a timestamp.
LOGGED = re.compile(r"^::\s+[\d-]+\s+[\d:.]+\s+(.*)$")
LINT_HEADING = "Lint warnings:"
LINT_LINE = re.compile(r"^- (\w+): (.+?)\s*(?:\(http\S+\))?$")


def _newest_log(logs=None):
    """snapcraft's most recent log, or None when there is none to read."""
    try:
        return max(Path(logs or SNAPCRAFT_LOGS).glob("*.log"),
                   key=lambda p: p.stat().st_mtime)
    except (OSError, ValueError):
        return None


def lint_findings(logs=None):
    """What snapcraft's own linters said about the snap it has just packed."""
    newest = _newest_log(logs)
    if newest is None:
        return []
    found, reading = [], False
    for line in newest.read_text(errors="replace").splitlines():
        logged = LOGGED.match(line)
        text = logged.group(1) if logged else line
        if text.strip() == LINT_HEADING:
            # Only the last run's block: the file holds more than one build.
            found, reading = [], True
            continue
        if not reading:
            continue
        one = LINT_LINE.match(text.strip())
        if one:
            found.append((one.group(1), one.group(2)))
        else:
            reading = False
    return found


def stale_instance():
    """The container a wedged run left behind, read from snapcraft's log."""
    newest = _newest_log()
    if newest is None:
        return ""
    text = newest.read_text(errors="replace")
    if STALE_INSTANCE not in text:
        return ""
    found = re.search(r"Failed to add disk to instance '([^']+)'", text)
    return found.group(1) if found else ""


def drop_instance(name):
    """Delete a build container, so the next build starts from a clean one."""
    return subprocess.run(["lxc", "--project", "snapcraft", "delete", "-f", name],
                          capture_output=True).returncode == 0


def snapcraft_preflight(destructive=False, reporter=None):
    """What is worth saying before a build that takes minutes, not after."""
    if not shutil.which("snapcraft"):
        die("snapcraft is not installed: sudo snap install snapcraft --classic")
    if destructive:
        return
    lxd = shutil.which("lxc") and subprocess.run(
        ["lxc", "list"], capture_output=True).returncode == 0
    if not lxd:
        (reporter or PlainReporter()).warn(
            "LXD is not answering, and it is snapcraft's default backend:\n"
            "           sudo snap install lxd\n"
            "           sudo lxd init --auto\n"
            '           sudo usermod -aG lxd "$USER"   # then: newgrp lxd')
