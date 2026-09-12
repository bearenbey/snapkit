"""Projects that exist already: importing one, and writing one back out."""

import contextlib
import io
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from snapforge import db, project
from snapforge.report import Reporter

from .harness import check, same, patched, env, raises


@check("a relative --dir is recorded as the directory it meant")
def _():
    # "myproj" was stored as written, and read from every later cwd.
    origin = project.File(path=Path("/nowhere/tool-1.0.tar.gz"), version="1.0")
    chosen = SimpleNamespace(kind="archive", name="tool-1.0.tar.gz")
    payload = SimpleNamespace(version="1.0", summary="", command="bin/tool",
                              traits={"terminal"})
    plan_ = SimpleNamespace(origin=origin, chosen=chosen, name="tool")
    snap = project._record(plan_, payload, "myproj")
    same(Path(snap.directory), (Path.cwd() / "myproj").resolve())
    home = project._record(plan_, payload, "~/myproj")
    same(Path(home.directory), Path(os.path.expanduser("~/myproj")).resolve())


@check("a project deleted from disk comes back from the register, icon and all")
def _():
    # The icon is kept beside the recipe, or the restored one names nothing.
    with tempfile.TemporaryDirectory() as home, env("SNAPKIT_HOME", home):
        store = db.Database()
        icon_source = Path(home) / "source.png"
        icon_source.write_bytes(b"\x89PNG\r\n\x1a\n fake")
        snap = db.Snap(name="demo", repo="a/b", version="1.0",
                       icon="snap/gui/demo.png",
                       recipe_text="name: demo\nicon: snap/gui/demo.png\n")
        store.add(snap)
        snap.keep_icon(icon_source)
        reporter = Reporter()
        project.write(snap, reporter)
        assert (snap.path / "snap/gui/demo.png").is_file()

        shutil.rmtree(snap.path)
        assert not snap.path.exists(), "the project is still there"

        project.package(snap, reporter, build_it=False)
        same((snap.path / "snap/snapcraft.yaml").read_text(),
             snap.snapcraft_yaml, "the recipe did not come back")
        assert (snap.path / "snap/gui/demo.png").is_file(), \
            "the icon did not come back"
        # and removing the snap takes the kept icon with it
        kept = snap.kept_icon
        assert kept and kept.is_file()
        store.remove("demo")
        assert not kept.exists(), "the kept icon outlived the record"


@check("an existing project can be read into a record")
def _():
    from snapforge import adopt
    with tempfile.TemporaryDirectory() as work:
        directory = Path(work) / "demo-snap"
        (directory / "snap").mkdir(parents=True)
        (directory / "snap/snapcraft.yaml").write_text(
            "name: demo\nbase: core24\nconfinement: classic\n"
            "grade: devel\nsummary: a demonstration\n"
            "description: |\n  first line\n  second line\n"
            "parts:\n  demo:\n    source: "
            "https://github.com/a/b/releases/download/v2.3.4/demo-2.3.4-amd64.deb\n")
        (directory / "README.md").write_text("see https://github.com/a/b\n")
        (directory / "build.py").write_text("#!/usr/bin/env python3\n")

        snap, recipe, is_snapcraft, confirmed = adopt.read(directory)
        same(snap.name, "demo")
        same(snap.version, "2.3.4", "the version was not read off the source")
        same(snap.kind, "deb")
        same(snap.confinement, "classic")
        same(snap.grade, "devel")
        same(snap.description.splitlines()[0], "first line", "block scalar")
        same(snap.build_with, "./build.py")
        same(confirmed, False, "an inferred repo was treated as confirmed")
        same(snap.asset_pattern, "", "an inferred repo enabled updates")
        same(snap.repo, "a/b", "the repo should still be recorded")

        snap, _, _, confirmed = adopt.read(directory, repo="a/b")
        same(confirmed, True)
        assert snap.asset_pattern, "a confirmed repo did not enable updates"

        # a project with no recipe at all is not a project
        with raises(adopt.NotAProject, "should have raised"):
            adopt.read(Path(work))


@check("a record whose project moved on is put back in line on load")
def _():
    # The record's version is a cache, and it was left a release behind.
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        directory = work / "demo-snap"
        (directory / "snap").mkdir(parents=True)
        recipe = directory / "snap/snapcraft.yaml"
        recipe.write_text("name: demo\nversion: '1.0.0'\nbase: core24\n")

        register = db.Database(work / "register")
        register.add(db.Snap(name="demo", version="1.0.0",
                             directory=str(directory)))

        # something else moves the project on
        recipe.write_text("name: demo\nversion: '1.1.0'\nbase: core24\n")

        reopened = db.Database(work / "register")
        same(reopened.get("demo").version, "1.1.0",
             "the record still reports the version it was imported at")
        same(reopened.resynced, [("demo", "1.0.0", "1.1.0")])
        same(db.Database(work / "register").resynced, [],
             "resyncing is not idempotent")

        # a project that is gone keeps the last version it was known on
        shutil.rmtree(directory)
        gone = db.Database(work / "register")
        same(gone.get("demo").version, "1.1.0",
             "a missing project should not blank the recorded version")
        same(gone.resynced, [])


@check("a record put right on load says so, rather than changing quietly")
def _():
    from snapforge import cli
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        directory = work / "demo-snap"
        (directory / "snap").mkdir(parents=True)
        recipe = directory / "snap" / "snapcraft.yaml"
        recipe.write_text("name: demo\nversion: '1.0.0'\nbase: core24\n")
        register = work / "register"
        db.Database(register).add(
            db.Snap(name="demo", version="1.0.0", directory=str(directory)))

        # something else moves the project on behind the register's back
        recipe.write_text("name: demo\nversion: '2.0.0'\nbase: core24\n")

        buffer = io.StringIO()
        with patched(db, home=lambda: register):
            with contextlib.redirect_stdout(buffer):
                cli.main(["list"])
        said = buffer.getvalue()
        assert "demo was recorded at 1.0.0" in said, said
        assert "2.0.0" in said, said

        # and nothing to say on the next command, because it is settled
        second = io.StringIO()
        with patched(db, home=lambda: register):
            with contextlib.redirect_stdout(second):
                cli.main(["list"])
        assert "was recorded at" not in second.getvalue(), second.getvalue()


@check("importing does not damage the project it imports")
def _():
    # write() used to overwrite a README and add an empty recipe.
    with tempfile.TemporaryDirectory() as work:
        directory = Path(work) / "hand-made"
        (directory / "overlay/meta").mkdir(parents=True)
        (directory / "overlay/meta/snap.yaml").write_text("name: handmade\n")
        (directory / "README.md").write_text("mine, do not touch\n")
        snap = db.Snap(name="handmade", directory=str(directory),
                       recipe_text="", build_with="./build.py")
        project.write(snap, Reporter())
        same((directory / "README.md").read_text(), "mine, do not touch\n",
             "the README was overwritten")
        assert not (directory / "snap").exists(), \
            "an empty snapcraft.yaml was written into it"


@check("packaging does not undo an edit made since the import")
def _():
    with tempfile.TemporaryDirectory() as work:
        directory = Path(work) / "demo"
        (directory / "snap").mkdir(parents=True)
        snap = db.Snap(name="demo", directory=str(directory),
                       recipe_text="name: demo\n")
        project.write(snap, Reporter())
        edited = "name: demo\n# edited by hand\n"
        (directory / "snap/snapcraft.yaml").write_text(edited)
        project.package(snap, Reporter(), build_it=False)
        same((directory / "snap/snapcraft.yaml").read_text(), edited,
             "the edit was written over")
        same(snap.snapcraft_yaml, edited, "the register did not learn it")


@check("from_name reads a version out of a source url")
def _():
    from snapforge.versions import from_name
    for source, want in (
            ("https://github.com/mpv-player/mpv/archive/refs/tags/v0.41.0.tar.gz", "0.41.0"),
            ("https://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz", "9.0.1"),
            ("https://github.com/o/o/releases/download/v0.32.15/o-linux.tar.zst", "0.32.15"),
            ("https://github.com/irssi/irssi/releases/download/1.4.5/irssi-1.4.5.tar.xz", "1.4.5"),
            ("./sublime-text_build-4200_amd64.deb", "4200")):
        same(from_name(source, ""), want, source)
