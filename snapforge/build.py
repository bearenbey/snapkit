"""Assembling a snap tree by hand, for the projects snapcraft cannot build.

Most recipes are handed to snapcraft, which does all of this and more. Some
cannot be: a core24 snap needs an LXD or Multipass backend, and a project
whose whole content is an upstream binary being restaged does not need a
build container to begin with -- nothing from the host would end up in the
snap. Those projects assemble a `prime/` tree themselves and call `snap
pack`, and what they have in common is here.

A project that does that keeps a `pack.py` beside its recipe, exposing one
function:

    def build(p):                 # p is a Build, already in the project
        tarball = p.artifact("floorp-linux-x86_64.tar.xz")
        prime = p.fresh_prime("usr/lib")
        p.run("tar", "xf", tarball, "-C", prime / "usr/lib")
        p.copy_overlay("meta/snap.yaml")
        p.gnome_helpers()
        return p.pack()

`snapkit build <name>` imports that and calls it. The dependency runs this
way round on purpose: the project used to import the tooling off a relative
path, which stops working the moment the tooling is installed as a snap, and
meant every project carried four lines of `sys.path` before it could say
anything about itself. What is left in a `pack.py` is the part that is that
project's alone.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .net import download, sha256_file
from .versions import deb_compare, yaml_version

# Where the desktop builds get their GTK and font helpers from.
GNOME_SNAP = Path("/snap/gnome-46-2404/current")


class BuildError(Exception):
    """Something the build cannot go on without."""


def _colour(code, text):
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def say(text):
    print(f"{_colour(36, '==>')} {text}", flush=True)


def note(text):
    print(f"    {text}", flush=True)


def warn(text):
    print(f"{_colour(33, 'warning:')} {text}", file=sys.stderr, flush=True)


def die(text):
    raise BuildError(text)


def stream(command, reporter=None, **kwargs):
    """Run a command, handing each line it writes to the reporter.

    stderr is folded into stdout because that is how it reads on a terminal:
    snapcraft writes its progress to one and its warnings to the other, and
    interleaved is the order things actually happened in.

    With no reporter the command keeps the terminal to itself, which is what
    the plain CLI wants and what every caller used to get.
    """
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

    def __init__(self, app, directory, reporter=None):
        self.app = app
        self.directory = Path(directory).resolve()
        os.chdir(self.directory)
        self.prime = self.directory / "prime"
        # Where the commentary goes. None prints it; the dashboard takes it.
        self.reporter = reporter

    # -- saying things: on the Build, so a pack.py needs no import ----------

    def say(self, text):
        if self.reporter:
            self.reporter.step(text)
        else:
            say(text)

    def note(self, text):
        if self.reporter:
            self.reporter.detail(text)
        else:
            note(text)

    def warn(self, text):
        if self.reporter:
            self.reporter.warn(text)
        else:
            warn(text)

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
        """Read back rather than assumed: after an update the packaging is the
        record of which release this is."""
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
        """The upstream file this build consumes.

        Named by the caller, or the one file in the project directory that
        matches -- an update leaves exactly one behind, having removed the
        superseded one. Looked up rather than hard-coded, so an update that
        lands mid-build changes what is packed, not what is opened.
        """
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
        """Refuse to pack a snap that would advertise a version its payload
        does not have."""
        if found != self.version:
            die(f"version mismatch: {self.yaml.name} says {self.version}, "
                f"{what} ships {found}")
        return found

    # -- running things ------------------------------------------------------

    def run(self, *command, **kwargs):
        kwargs.setdefault("check", True)
        kwargs.setdefault("cwd", self.directory)
        argv = [str(c) for c in command]

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
        """Import another file of this project's own build, by location.

        A project whose packaging runs to more than one file -- Transmission
        assembles a whole build root before it compiles anything -- keeps the
        rest beside its pack.py and asks for it here, rather than re-entering
        Python as a subprocess to reach code that is sitting next to it.
        """
        return pack_module(self.directory, relative)

    def deb_compare(self, a, b):
        """dpkg's version ordering, for a project reading an apt index.

        Not `sort -V`: a Debian version has an epoch, a revision, and a `~`
        that sorts before the empty string so that 1.0~rc1 comes before 1.0.
        """
        return deb_compare(a, b)

    # -- fetching ------------------------------------------------------------

    def download(self, url, destination, sha=""):
        """Fetch a file, checking it against a checksum if one is known.

        Here so that a project vendoring a library out of an archive mirror
        -- which is the only reason any of them fetch anything of their own,
        the release itself having been fetched by the update -- does not have
        to reach for the tooling to do it.
        """
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
        """Copy overlay/<path> to prime/<path>, which is what overlay/ is for."""
        for name in relative:
            self.copy(self.directory / "overlay" / name, self.prime / name)

    def configure_hook(self):
        """snapd runs a configure hook if one is declared; this is the stub the
        recipes declare so that `snap set` does not fail."""
        hook = self.prime / "meta" / "hooks" / "configure"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/true\n")
        # +x on top of the umask, as `printf > file` then chmod +x would give.
        self.make_executable(hook)

    def gnome_helpers(self, gnome=None):
        """The desktop/font helpers snapcraft's `gnome` extension copies out of
        the matching SDK -- taken straight from the platform snap here."""
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

    def missing_libraries(self, binary):
        """What ldd cannot resolve for a binary. Empty when everything is
        there; anything listed fails at runtime, not at pack time."""
        done = subprocess.run(["ldd", str(binary)], capture_output=True, text=True)
        return [line.split()[0] for line in done.stdout.splitlines()
                if "not found" in line]

    def warn_missing(self, binary, hint=""):
        """Say what a classic snap will not find on this host.

        A warning rather than an error, and it is about the host rather than
        about the snap: a classic snap links against whatever is out there, so
        the same pack can be fine on the next machine and broken on this one.
        Either way it fails at runtime, where it is hard to read, so it is
        worth saying at pack time, where it is not.
        """
        missing = self.missing_libraries(binary)
        if missing:
            warn("these libraries are missing on this host:\n"
                 + "\n".join(f"           {name}" for name in missing)
                 + (f"\n         {hint}" if hint else ""))
        return missing

    # -- output --------------------------------------------------------------

    def pack(self, name=None, arch="amd64"):
        filename = name or f"{self.app}_{self.version}_{arch}.snap"
        say(f"packing version {self.version}")
        self.run("snap", "pack", self.prime, f"--filename={filename}", ".")
        built = self.directory / filename
        if not built.is_file():
            die(f"snap pack finished but {filename} was not produced")
        note(f"{filename}  ({built.stat().st_size / 1e6:.0f} MB)")
        return built


# -- finding and running a project's pack.py ----------------------------------

def pack_module(directory, filename="pack.py"):
    """Import a project's pack.py, without it being on the path.

    Loaded by location rather than by name so that twenty projects can each
    have a `pack.py` without the first one imported shadowing the rest, and
    so that nothing has to be installed or `sys.path`-ed for a project to be
    built from wherever it happens to sit.
    """
    path = Path(directory) / filename
    if not path.is_file():
        die(f"no {filename} in {Path(directory).name}")
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


def run_pack(app, directory, filename="pack.py", reporter=None):
    """Assemble and pack one project. Returns the .snap it produced."""
    here = Path.cwd()
    try:
        module = pack_module(directory, filename)
        if not hasattr(module, "build"):
            die(f"{Path(directory) / filename} defines no build(project) function")
        return module.build(Build(app, directory, reporter))
    finally:
        os.chdir(here)


def snapcraft_preflight(destructive=False):
    """What is worth saying before a build that takes minutes, not after.

    snapcraft says the second half of this itself, but only once it has
    pulled the recipe apart first -- and the fix is three commands rather
    than one. A warning and not an error: --destructive-mode and a host whose
    base matches need no backend at all.
    """
    if not shutil.which("snapcraft"):
        die("snapcraft is not installed: sudo snap install snapcraft --classic")
    if destructive:
        return
    lxd = shutil.which("lxc") and subprocess.run(
        ["lxc", "list"], capture_output=True).returncode == 0
    if not lxd:
        warn("LXD is not answering, and it is snapcraft's default backend:\n"
             "           sudo snap install lxd\n"
             "           sudo lxd init --auto\n"
             '           sudo usermod -aG lxd "$USER"   # then: newgrp lxd')
