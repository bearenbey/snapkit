"""Assembling a snap tree by hand, for the projects snapcraft cannot build."""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .arch import host as host_arch
from .inspect import missing_libraries
from .net import download, sha256_file
from .report import PlainReporter
from .versions import deb_compare, yaml_version

# Where the desktop builds get their GTK and font helpers from.
GNOME_SNAP = Path("/snap/gnome-46-2404/current")

# A part name sits two spaces in, its settings deeper than that.
PART_NAME = re.compile(r"^  ([A-Za-z0-9][\w.+-]*):\s*$")
PART_SOURCE = re.compile(r"^\s+source:\s*(.+?)\s*$")


def file_source_parts(yaml_path):
    """The parts fed from a file here, as (part, file) pairs."""
    yaml_path = Path(yaml_path)
    if not yaml_path.is_file():
        return []
    directory = yaml_path.parent.parent
    found, name, in_parts = [], "", False
    for line in yaml_path.read_text(encoding="utf-8",
                                    errors="replace").splitlines():
        if line.strip() and not line.startswith(" "):
            in_parts, name = line.startswith("parts:"), ""
            continue
        if not in_parts:
            continue
        part = PART_NAME.match(line)
        if part:
            name = part.group(1)
            continue
        source = PART_SOURCE.match(line)
        if name and source:
            given = directory / source.group(1).strip("\'\"")
            if given.is_file():
                found.append((name, given))
    return found


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
        self.prime = self.directory / "prime"
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
        """The upstream file this build consumes."""
        if given:
            return self.need_file(given)
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

    def check_version(self, found, what):
        """Refuse to pack a snap whose payload is not the version it claims."""
        if found != self.version:
            die(f"version mismatch: {self.yaml.name} says {self.version}, "
                f"{what} ships {found}")
        return found

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
        if wants_output or not getattr(self.reporter, "captures_output", False):
            return subprocess.run(argv, **kwargs)

        check = kwargs.pop("check")
        done = stream(argv, self.reporter, **kwargs)
        if check and done.returncode != 0:
            raise subprocess.CalledProcessError(done.returncode, argv)
        return done

    def capture(self, *command, **kwargs):
        done = self.run(*command, capture_output=True, text=True, **kwargs)
        return done.stdout.strip()

    # -- splitting a build across more than one file ---------------------------

    def module(self, relative):
        """Import another file of this project's own build, by location."""
        return pack_module(self.directory, relative)

    def deb_compare(self, a, b):
        """dpkg's version ordering, for a project reading an apt index."""
        return deb_compare(a, b)

    # -- fetching ------------------------------------------------------------

    def download(self, url, destination, sha=""):
        """Fetch a file, checking it against a checksum if one is known."""
        return download(url, Path(destination), sha)

    def sha256(self, path):
        return sha256_file(Path(path))

    # -- the prime tree ------------------------------------------------------

    def fresh_prime(self, *subdirectories):
        """An empty prime/, with these directories in it."""
        if self.prime.exists():
            shutil.rmtree(self.prime)
        self.prime.mkdir()
        for name in subdirectories:
            (self.prime / name).mkdir(parents=True, exist_ok=True)
        return self.prime

    def copy(self, source, destination, executable=False):
        """Copy one file into the prime tree, making its parent if needed."""
        source, destination = Path(source), Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if executable:
            destination.chmod(destination.stat().st_mode | 0o111)
        return destination

    def copy_overlay(self, *relative):
        """Copy overlay/<path> into prime/<path>."""
        for name in relative:
            self.copy(self.directory / "overlay" / name, self.prime / name)

    def configure_hook(self):
        """Write the stub configure hook snapd runs when one is declared."""
        hook = self.prime / "meta" / "hooks" / "configure"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/true\n")
        # +x on top of the umask, as `printf > file` then chmod +x would give.
        self.make_executable(hook)

    def gnome_helpers(self, gnome=None):
        """Copy the desktop and font helpers out of the gnome platform snap."""
        gnome = gnome or self.gnome_platform()
        chain = self.prime / "snap" / "command-chain"
        chain.mkdir(parents=True, exist_ok=True)
        for name in ("desktop-launch", "hooks-configure-fonts"):
            self.copy(gnome / "command-chain" / name, chain / name, executable=True)

    def make_executable(self, *paths):
        for path in paths:
            for one in ([path] if Path(path).is_file() else sorted(Path(path).glob("*"))):
                one = Path(one)
                if one.is_file():
                    one.chmod(one.stat().st_mode | 0o111)

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

    # -- output --------------------------------------------------------------

    def pack(self, name=None, arch=None):
        filename = name or (f"{self.app}_{self.version}_"
                            f"{arch or host_arch()}.snap")
        self.say(f"packing version {self.version}")
        self.run("snap", "pack", self.prime, f"--filename={filename}", ".")
        built = self.directory / filename
        if not built.is_file():
            die(f"snap pack finished but {filename} was not produced")
        self.note(f"{filename}  ({built.stat().st_size / 1e6:.0f} MB)")
        return built


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
        f"snapforge._pack.{Path(directory).name}", path)
    module = importlib.util.module_from_spec(spec)
    # Registered before running, so a pack.py can import from beside itself.
    sys.modules[spec.name] = module
    directory = str(Path(directory).resolve())
    sys.path.insert(0, directory)
    # A build should leave no __pycache__ behind in somebody's project.
    was_writing = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = was_writing
        if sys.path and sys.path[0] == directory:
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


def lint_findings(logs=None):
    """What snapcraft's own linters said about the snap it has just packed."""
    try:
        newest = max(Path(logs or SNAPCRAFT_LOGS).glob("*.log"),
                     key=lambda p: p.stat().st_mtime)
    except (OSError, ValueError):
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
    try:
        newest = max(SNAPCRAFT_LOGS.glob("*.log"), key=lambda p: p.stat().st_mtime)
    except (OSError, ValueError):
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
