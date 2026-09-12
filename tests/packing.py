"""The Build a pack.py is handed, and what it does around snapcraft."""

import os
import tempfile
import time
from pathlib import Path

from snapforge import build as buildlib
from snapforge import project
from snapforge.report import Reporter

from .harness import (check, same, subprocess_result, Quiet, Capture, patched,
                      raises, make_deb, elf_needing)


class Noted(Reporter):
    """A reporter that keeps what was noted and warned."""

    def __init__(self):
        self.notes, self.warnings = [], []

    def detail(self, text):
        self.notes.append(text)

    def warn(self, text):
        self.warnings.append(text)


class FakeSubprocess:
    """snapcraft and unsquashfs that do nothing, and say they did."""
    ran = []

    @classmethod
    def run(cls, argv, **kwargs):
        cls.ran.append(argv)
        return subprocess_result()


def a_build(here, yaml="name: demo\nversion: '1.0'\n", reporter=None):
    (here / "snap").mkdir(exist_ok=True)
    (here / "snap" / "snapcraft.yaml").write_text(yaml)
    was = os.getcwd()
    made = buildlib.Build("demo", here, reporter or Quiet())
    # A Build moves into its directory; the tests must not stay there
    # once the directory is gone.
    os.chdir(was)
    # The tools are not the subject, and CI has neither.
    made.need_tools = lambda *names: None
    return made


@check("pack hands back the snap for the recipe's version, whatever its arch")
def _():
    with tempfile.TemporaryDirectory() as home, \
            patched(buildlib, subprocess=FakeSubprocess):
        here = Path(home)
        build = a_build(here)
        (here / "demo_0.9_amd64.snap").write_bytes(b"old")
        try:
            build.pack()
            assert False, "nothing for 1.0, so it should have died"
        except buildlib.BuildError as exc:
            assert "demo_1.0_*.snap" in str(exc), str(exc)
        (here / "demo_1.0_arm64.snap").write_bytes(b"new")
        same(build.pack().name, "demo_1.0_arm64.snap")
        same(FakeSubprocess.ran[-1], ["snapcraft", "pack"])
        build.pack(clean=True)
        same(FakeSubprocess.ran[-2:], [["snapcraft", "clean"],
                                       ["snapcraft", "pack"]])


@check("a check that dies inside unpacked takes the snap with it")
def _():
    with tempfile.TemporaryDirectory() as home, \
            patched(buildlib, subprocess=FakeSubprocess):
        here = Path(home)
        build = a_build(here)
        snap = here / "demo_1.0_amd64.snap"
        snap.write_bytes(b"snap")
        with build.unpacked(snap) as root:
            assert not root.exists(), "unsquashfs ran nowhere"
        assert snap.is_file(), "a snap that passed was deleted"
        same(FakeSubprocess.ran[-1][0], "unsquashfs")

        try:
            with build.unpacked(snap):
                build.die("the payload is wrong")
            assert False, "should have raised"
        except buildlib.BuildError:
            pass
        assert not snap.exists(), "a snap that failed its check survived"

        # Only a refusal deletes it: a tool crashing is not a verdict.
        snap.write_bytes(b"snap")
        try:
            with build.unpacked(snap):
                raise RuntimeError("objdump exploded")
        except RuntimeError:
            pass
        assert snap.is_file(), "an error that was not a check deleted it"


@check("artifact takes the file the recipe names over an ambiguous glob")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        (here / "demo-1.0.tar.gz").write_text("old")
        (here / "demo-2.0.tar.gz").write_text("new")
        build = a_build(here)
        try:
            build.artifact("demo-*.tar.gz")
            assert False, "two candidates and no recipe naming one"
        except buildlib.BuildError as exc:
            assert "more than one" in str(exc), str(exc)

        named = a_build(here, "name: demo\nversion: '2.0'\nparts:\n"
                              "  app:\n    source: demo-2.0.tar.gz\n")
        same(named.artifact("demo-*.tar.gz").name, "demo-2.0.tar.gz")
        same(named.recipe_source("*.deb"), None, "nothing matches that")

        (here / "demo-2.0.tar.gz").unlink()
        try:
            named.artifact("demo-*.tar.gz")
            assert False, "the recipe names a file that is not here"
        except buildlib.BuildError as exc:
            assert "--force fetches it" in str(exc), str(exc)


@check("finish says --classic when the recipe does, and names the plugs")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        said = Noted()
        build = a_build(here, reporter=said)
        same(build.finish(here / "demo_1.0_amd64.snap", "camera"),
             here / "demo_1.0_amd64.snap", "finish hands the snap back")
        same(said.notes[-1],
             "install it with:\n"
             "      sudo snap install --dangerous demo_1.0_amd64.snap\n"
             "      sudo snap connect demo:camera")

        classic = a_build(here, "name: demo\nversion: '1.0'\n"
                                "confinement: classic\n", reporter=said)
        assert classic.classic
        classic.finish(here / "demo_1.0_amd64.snap")
        assert "--dangerous --classic demo_1.0_amd64.snap" in said.notes[-1]
        assert "connect" not in said.notes[-1], "no plugs were named"


@check("check_version can be told what the payload should say")
def _():
    with tempfile.TemporaryDirectory() as home:
        build = a_build(Path(home), "name: demo\nversion: '1.0-beta'\n")
        same(build.check_version("1.0", "the deb", expected="1.0"), "1.0")
        try:
            build.check_version("1.1", "the deb")
            assert False, "should have raised"
        except buildlib.BuildError as exc:
            assert "says 1.0-beta, the deb ships 1.1" in str(exc), str(exc)


@check("application_ini reads a Gecko payload, and an absent one is empty")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        build = a_build(here)
        ini = build.application_ini(here)
        same(ini.get("App", "Version", fallback=None), None)
        (here / "application.ini").write_text(
            "[App]\nVendor=Mozilla\nVersion=140.15.0\n"
            "RemotingName=firefox-esr\n[Gecko]\nMinVersion=140.15.0\n")
        ini = build.application_ini(here)
        same(ini.get("App", "Version"), "140.15.0")
        same(ini.get("App", "RemotingName"), "firefox-esr")
        same(ini.get("Gecko", "MinVersion"), "140.15.0")


@check("python_modules names what is staged under dist-packages")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        where = here / "usr/lib/python3.12/dist-packages"
        where.mkdir(parents=True)
        (where / "requests").mkdir()
        (where / "PIL").mkdir()
        (where / "six.py").write_text("")
        (where / "psutil-5.9.8.egg-info").mkdir()
        same(a_build(here).python_modules(here),
             {"requests", "PIL", "six", "psutil-5"})


@check("unprovided names what the payload links that nothing supplies")
def _():
    from snapforge import elf
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        tree = here / "opt" / "app"
        (tree / "lib").mkdir(parents=True)
        (tree / "lib" / "libown.so.1").write_bytes(elf_needing("libc.so.6"))
        (tree / "app").write_bytes(elf_needing(
            "libc.so.6", "libgtk-3.so.0", "libown.so.1", "libcuda.so.1",
            "libnowhere.so.3"))
        (tree / "notes.txt").write_text("not a binary")
        same(elf.needed(tree / "lib" / "libown.so.1"), ["libc.so.6"],
             "the fixture is an ELF the reader understands")
        build = a_build(here)
        same(build.unprovided(tree, tree / "app"),
             {"libnowhere.so.3": ["app"]})
        said = Noted()
        build = a_build(here, reporter=said)
        build.warn_unprovided(tree, tree / "app")
        assert "libnowhere.so.3" in said.warnings[-1], said.warnings
        assert "needed by app" in said.warnings[-1], said.warnings
        same(build.needed(tree / "app")[:2], ["libc.so.6", "libgtk-3.so.0"])
        try:
            build.needed(tree / "notes.txt")
            assert False, "should have raised"
        except buildlib.BuildError as exc:
            assert "not an ELF" in str(exc), str(exc)


@check("what a classic snap will not find on the host is read, not run")
def _():
    from snapforge import inspect as inspectlib
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        (here / "libown.so.1").write_bytes(elf_needing("libc.so.6"))
        (here / "app").write_bytes(elf_needing(
            "libc.so.6", "libown.so.1", "libgone.so.2"))
        with patched(inspectlib, host_libraries=lambda: frozenset({"libc.so.6"})):
            same(inspectlib.missing_libraries(here / "app"), ["libgone.so.2"])
            # A host that cannot be asked reports nothing rather than everything.
        with patched(inspectlib, host_libraries=lambda: frozenset()):
            same(inspectlib.missing_libraries(here / "app"), [])


@check("a recipe's source: lines are read once, for everyone who asks")
def _():
    from snapforge import recipe
    text = ("name: demo\nsource: not-a-part\nparts:\n"
            "  app:\n    plugin: dump\n    source: 'demo-1.0.tar.gz'\n"
            "  gtk:\n    source: https://x/gtk.tar.xz\n    source-type: tar\n"
            "  nil:\n    plugin: nil\napps:\n  demo:\n    command: bin/demo\n")
    same(recipe.sources(text), [("app", "demo-1.0.tar.gz"),
                                ("gtk", "https://x/gtk.tar.xz")])
    same(recipe.sources("name: x\n"), [])


@check("deb_field reads the control stanza of a .deb")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        deb = make_deb(here / "demo_2.5.0_amd64.deb", "demo", "2.5.0")
        build = a_build(here)
        same(build.deb_field(deb, "Version"), "2.5.0")
        same(build.deb_field(deb, "Nothing"), "")


@check("a pack.py is imported and called, not run as a program")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        (here / "pack.py").write_text(
            "MARK = []\n"
            "def build(project):\n"
            "    MARK.append(project.app)\n"
            "    return project.directory / 'demo_1_amd64.snap'\n")
        made = buildlib.run_pack("demo", here)
        same(made.name, "demo_1_amd64.snap")

        # Two pack.py files, and the first must not stand in for the second.
        other = Path(home) / "other"
        other.mkdir()
        (other / "pack.py").write_text(
            "def build(project):\n    return project.app\n")
        same(buildlib.run_pack("second", other), "second")


@check("a pack.py with no build() says so rather than doing nothing")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        (here / "pack.py").write_text("VERSION = 1\n")
        try:
            buildlib.run_pack("demo", here)
            assert False, "should have raised"
        except buildlib.BuildError as exc:
            assert "build(project)" in str(exc), str(exc)


@check("a pack.py outside the project is refused, not imported")
def _():
    from snapforge import build as buildlib
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        (here / "project").mkdir()
        # Importing runs it, and the name can come off a fetched record.
        (here / "outside.py").write_text("raise SystemExit('ran')\n")
        try:
            buildlib.pack_module(here / "project", "../outside.py")
            assert False, "a file outside the project was imported"
        except buildlib.BuildError as exc:
            assert "outside" in str(exc), str(exc)
        # A file of the project, in a subdirectory, is still fine.
        (here / "project" / "lib").mkdir()
        (here / "project" / "lib" / "helper.py").write_text("value = 1\n")
        same(buildlib.pack_module(here / "project", "lib/helper.py").value, 1)


@check("a pack.py is left where it was, whatever it does to the cwd")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        (here / "pack.py").write_text(
            "def build(project):\n    return project.directory\n")
        was = os.getcwd()
        buildlib.run_pack("demo", here)
        same(os.getcwd(), was, "the cwd came back")


@check("--destructive-mode reaches the snapcraft a pack.py runs")
def _():
    from snapforge import build as buildlib

    seen = []

    class FakeSubprocess:
        @staticmethod
        def run(argv, **kwargs):
            seen.append(argv)
            return subprocess_result()

    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        (here / "pack.py").write_text(
            "def build(project):\n"
            "    project.run('snapcraft', 'pack')\n"
            "    project.run('tar', 'xf', 'thing')\n")
        with patched(buildlib, subprocess=FakeSubprocess):
            buildlib.run_pack("demo", here,
                              snapcraft_flags=["--destructive-mode"])

    same(seen[0], ["snapcraft", "pack", "--destructive-mode"],
         "the flag did not reach snapcraft")
    same(seen[1], ["tar", "xf", "thing"],
         "the flag was put on something that is not snapcraft")


@check("what snapcraft's linters found is read back out of its own log")
def _():
    from snapforge import build as buildlib
    with tempfile.TemporaryDirectory() as home:
        logs = Path(home)
        (logs / "old.log").write_text(
            ":: 2026-09-03 21:00:00.000 Lint warnings:\n"
            ":: 2026-09-03 21:00:00.000 - gpu: a/b.so: from an older build.\n")
        newest = logs / "new.log"
        newest.write_text(
            ":: 2026-09-03 22:00:00.000 Running linters...\n"
            ":: 2026-09-03 22:00:00.000 Lint warnings:\n"
            ":: 2026-09-03 22:00:00.000 - library: lib/a.so: missing "
            "dependency 'libjvm.so'. (https://x)\n"
            ":: 2026-09-03 22:00:00.000 - library: b.so: unused library "
            "'usr/lib/b.so'. (https://x)\n"
            ":: 2026-09-03 22:00:00.000 Creating snap package...\n")
        os.utime(newest, (2 ** 31, 2 ** 31))
        found = buildlib.lint_findings(logs)

    same(len(found), 2, f"read the wrong block: {found}")
    same([k for k, _ in found], ["library", "library"])
    # A missing dependency will not start; an unused one is only weight.
    same(project._advice(*found[0]), "missing")
    same(project._advice(*found[1]), "unused")
    same(project.LINT_ADVICE["missing"][0], "warn")
    same(project.LINT_ADVICE["unused"][0], "detail")


@check("a build with no linter findings says nothing about them")
def _():
    from snapforge import build as buildlib
    with tempfile.TemporaryDirectory() as home:
        same(buildlib.lint_findings(Path(home)), [])
        (Path(home) / "a.log").write_text(":: 2026 00:00 Creating snap\n")
        same(buildlib.lint_findings(Path(home)), [])


@check("a wedged build container is recognised from snapcraft's own log")
def _():
    with tempfile.TemporaryDirectory() as home:
        logs = Path(home) / "log"
        logs.mkdir()
        with patched(buildlib, SNAPCRAFT_LOGS=logs):
            same(buildlib.stale_instance(), "", "no logs, nothing to find")

            (logs / "old.log").write_text("nothing wrong here\n")
            same(buildlib.stale_instance(), "", "a clean log is not a wedge")

            # The CLI never sees snapcraft's output, so read its log.
            (logs / "new.log").write_text(
                "Failed to add disk to instance 'snapcraft-demo-amd64-1234'.\n"
                "* Command standard error output: b'Error: The device already exists'\n")
            same(buildlib.stale_instance(), "snapcraft-demo-amd64-1234")


@check("a part is cleaned when the file under it is newer than the snap")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        (here / "snap").mkdir()
        (here / "overlay").mkdir()
        (here / "app-linux-x86_64.tar.xz").write_bytes(b"payload")
        (here / "snap" / "snapcraft.yaml").write_text(
            "name: app\n"
            "source-code: https://example.invalid/app\n"
            "parts:\n"
            "  app:\n"
            "    plugin: dump\n"
            "    source: app-linux-x86_64.tar.xz\n"
            "  overlay:\n"
            "    plugin: dump\n"
            "    source: overlay\n"
            "  upstream:\n"
            "    plugin: dump\n"
            "    source: https://example.invalid/app.tar.gz\n")

        same(buildlib.stale_parts(here), [], "nothing packed yet")

        packed = here / "app_1.0_amd64.snap"
        packed.write_bytes(b"squashfs")
        same(buildlib.stale_parts(here), [], "the snap is the newer of the two")

        # What `snapkit update` does: the same filename, new contents.
        (here / "app-linux-x86_64.tar.xz").write_bytes(b"a later release")
        os.utime(here / "app-linux-x86_64.tar.xz",
                 (packed.stat().st_mtime + 10,) * 2)
        same(buildlib.stale_parts(here), ["app"],
             "only the part fed from the replaced file")


@check("a build's own output is handed to the reporter line by line")
def _():

    # stderr folded into stdout: interleaved is the order it happened in.
    seen = Capture()
    done = buildlib.stream(["bash", "-c", "echo one; echo two >&2; echo three"],
                           seen)
    same(seen.lines, ["one", "two", "three"])
    same(done.returncode, 0)

    # A failure is returned rather than swallowed by the reporting.
    after = Capture()
    same(buildlib.stream(["bash", "-c", "echo boom; exit 7"], after).returncode, 7)
    same(after.lines, ["boom"])


@check("a reporter that does not capture leaves the command its terminal")
def _():
    from snapforge.report import PlainReporter

    # The CLI wants snapcraft on a real tty, so nothing is piped.
    same(PlainReporter().captures_output, False)
    same(buildlib.stream(["bash", "-c", "exit 3"], PlainReporter()).returncode, 3)


@check("a progress bar redrawing itself is one line, not hundreds")
def _():

    # Read as text, universal newlines split every \r redraw into a line.
    seen = Capture()
    buildlib.stream(["bash", "-c", r"printf '10%%\r50%%\r100%%\n'"], seen)
    same(seen.lines, ["100%"])


@check("cancelling a build kills it rather than waiting for it")
def _():

    class Stop(Exception):
        pass

    class Stopper(Reporter):
        captures_output = True

        def output(self, line):
            raise Stop()

    # Not KeyboardInterrupt: Popen.__exit__ special-cases it and gives up.
    started = time.time()
    with raises(Stop, "should have raised"):
        buildlib.stream(["bash", "-c", "echo go; sleep 30"], Stopper())
    assert time.time() - started < 5, "the child was waited on, not killed"
