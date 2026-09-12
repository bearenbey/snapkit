"""Updating: resolving an upstream, and rewriting the project onto it."""

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from snapforge import db, rewrite, sources, update

from .harness import check, same, patched, raises


@check("prune names every build but the newest, and superseded files")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        for name, age in (("demo_1.0_amd64.snap", 3), ("demo_2.0_amd64.snap", 2),
                          ("demo_3.0_amd64.snap", 1)):
            path = here / name
            path.write_bytes(b"x")
            os.utime(path, (time.time() - age, time.time() - age))
        (here / "demo-2.0.tar.gz").write_bytes(b"x")
        (here / "demo-3.0.tar.gz").write_bytes(b"x")
        (here / "other_1.0_amd64.snap").write_bytes(b"x")     # not this snap's
        snap = db.Snap(name="demo", directory=str(here), style="artifact",
                       asset="demo-3.0.tar.gz", asset_glob="demo-*.tar.gz")
        same(sorted(p.name for p in update.prunable(snap)),
             ["demo-2.0.tar.gz", "demo_1.0_amd64.snap", "demo_2.0_amd64.snap"])
        # a recipe-style snap has no files of its own to weigh up
        recipe_style = db.Snap(name="demo", directory=str(here), style="recipe")
        same(sorted(p.name for p in update.prunable(recipe_style)),
             ["demo_1.0_amd64.snap", "demo_2.0_amd64.snap"])
        # and one whose directory is gone has nothing to prune
        same(update.prunable(db.Snap(name="gone", directory=str(here / "no"))), [])


@check("prune deletes what it listed, and only when told to")
def _():
    from snapforge import cli
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        store = db.Database(here / "snapkit.json")
        project_dir = here / "demo"
        project_dir.mkdir()
        old, new = project_dir / "demo_1.0_amd64.snap", project_dir / "demo_2.0_amd64.snap"
        old.write_bytes(b"x")
        time.sleep(0.01)
        new.write_bytes(b"x")
        store.add(db.Snap(name="demo", directory=str(project_dir)))
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_prune(store, SimpleNamespace(rest=[], yes=True), None)
        assert not old.exists(), "the old build stayed"
        assert new.exists(), "the newest build went too"


@check("every upstream shape is reachable by the name a record gives it")
def _():
    same(sorted(sources.RESOLVERS),
         ["apt", "index", "local", "redirect", "tag-archive"])
    for bad in ("", "github", "ftp"):
        with raises(sources.BadUpstream, f"{bad!r} should not resolve"):
            sources.resolve({"kind": bad})


@check("an apt index answers with the newest amd64 stanza and its checksum")
def _():
    index = ("Package: demo\nArchitecture: amd64\nVersion: 1.0\n"
             "Filename: pool/d/demo_1.0_amd64.deb\nSHA256: aa\n\n"
             "Package: demo\nArchitecture: amd64\nVersion: 1.10\n"
             "Filename: pool/d/demo_1.10_amd64.deb\nSHA256: bb\n\n"
             "Package: demo\nArchitecture: arm64\nVersion: 2.0\n"
             "Filename: pool/d/demo_2.0_arm64.deb\nSHA256: cc\n\n"
             # Under sort -V a 1.11~beta.1 reads as newer than the 1.11.
             "Package: demo\nArchitecture: amd64\nVersion: 1.11~beta.1\n"
             "Filename: pool/d/demo_1.11b_amd64.deb\nSHA256: dd\n\n"
             "Package: demo\nArchitecture: amd64\nVersion: 1.11\n"
             "Filename: pool/d/demo_1.11_amd64.deb\nSHA256: ee\n")
    # An apt index is read by versions.apt_stanza, through its own import.
    from snapforge import versions
    with patched(versions, get_text=lambda url, **k: index):
        found = sources.resolve({"kind": "apt", "base": "http://x",
                                 "package": "demo", "index": "http://x/P"})
    # The release, not the beta, and not the arm64 build of either.
    same(found.version, "1.11")
    same(found.sha, "ee")
    same(found.url, "http://x/pool/d/demo_1.11_amd64.deb")

    assert versions.deb_compare("1.11~beta.1", "1.11") < 0, "~ sorts first"
    assert versions.version_key("1.11~beta.1") > versions.version_key("1.11"), \
        "and sort -V is the ordering that would get this wrong"


@check("deb_compare answers what dpkg answers, wherever dpkg can be asked")
def _():
    # deb_compare saves a fork per comparison, while it still agrees.
    from snapforge import versions
    if not shutil.which("dpkg"):
        return                 # nothing to compare against on this host

    # Only versions dpkg calls well formed, or it answers about its parser.
    pool = ("1.0", "1.0-1", "1:1.0", "0:1.0", "1.0~rc1", "1.0~", "1.0a",
            "1.00", "1.0.0", "2.0", "1.0-1ubuntu1", "1.0+git20240101",
            "10", "9", "1.0~beta.2", "3.10.0~beta.2", "3.10.0", "0",
            "1:0", "1.0-1~exp1", "1.2.3+ds-2", "1.0-1+deb12u1")

    def dpkg(a, op, b):
        return subprocess.run(["dpkg", "--compare-versions", a, op, b],
                              stderr=subprocess.DEVNULL).returncode == 0

    for a in pool:
        for b in pool:
            mine = versions.deb_compare(a, b)
            theirs = 0 if dpkg(a, "eq", b) else (-1 if dpkg(a, "lt", b) else 1)
            same((mine > 0) - (mine < 0), theirs, f"{a!r} against {b!r}")


@check("a directory listing answers with the newest release in it")
def _():
    listing = ('emacs-29.4.tar.xz" emacs-30.2.tar.xz" emacs-9.1.tar.xz" '
               'emacs-31.1.tar.xz" emacs-31.1.tar.xz.sig"')
    with patched(sources, get_text=lambda url, **k: listing):
        found = sources.resolve({
            "kind": "index", "url": "https://ftp.gnu.org/gnu/emacs/",
            "pattern": r'emacs-(\d+\.\d+(?:\.\d+)?)\.tar\.xz"',
            "asset": "emacs-{version}.tar.xz"})
    # 31.1 over 9.1: digit runs compare as numbers, not as text.
    same(found.version, "31.1")
    same(found.url, "https://ftp.gnu.org/gnu/emacs/emacs-31.1.tar.xz")


@check("a download endpoint's redirect is read for the version")
def _():
    with patched(sources, head_location=lambda url, **k:
                 "https://dl.discordapp.net/apps/linux/1.0.155/discord-1.0.155.deb"):
        found = sources.resolve({
            "kind": "redirect", "url": "https://discord.com/api/download",
            "pattern": r"/apps/linux/([^/]+)/",
            "asset": "discord-{version}.deb",
            "download": "https://dl.discordapp.net/apps/linux/{version}/{asset}"})
    same(found.version, "1.0.155")
    same(found.asset, "discord-1.0.155.deb")


@check("a tag archive is built out of the tag, not out of an asset list")
def _():
    from snapforge import github
    with patched(github, latest_tag=lambda repo: "v0.41.0"):
        found = sources.resolve({
            "kind": "tag-archive", "repo": "mpv-player/mpv", "prefix": "v",
            "asset": "mpv-{version}.tar.gz",
            "download": "https://github.com/mpv-player/mpv/archive/"
                        "refs/tags/{tag}.tar.gz"})
    same(found.version, "0.41.0")
    same(found.tag, "v0.41.0")
    same(found.url, "https://github.com/mpv-player/mpv/archive/refs/"
                    "tags/v0.41.0.tar.gz")


@check("a version is replaced as a version, and not inside another word")
def _():
    # A rule a shade too eager silently edits the rest of a README.
    spelled = ("1.4.0", "1.21.15b", "4.3", "0.10.2", "0.0.75", "4180",
               "0.4.11.1", "30.1", "0.25.2-beta", "1.0", "3.10", "24")
    replaced = ("version: '{v}'", "app_{v}_amd64.snap", "app-{v}.tar.gz",
                "v{v}", "V{v}", "see {v} here", "/download/{v}/", "{v}",
                "  {v}", "{v}  ", "install ./demo_{v}_amd64.snap",
                "https://x/releases/download/v{v}/app-{v}.tar.gz")
    # A word in front means a name ending in digits, as core24 is.
    left = ("9{v}", "{v}9", "{v}.9", "1{v}", "core{v}", "python{v}",
            "gtk{v}", "x{v}", "release{v}")

    for version in spelled:
        newer = "9" + version
        for shape in replaced:
            same(rewrite.replace_version(shape.format(v=version),
                                         version, newer),
                 shape.format(v=newer), f"{version} in {shape!r}")
        for shape in left:
            text = shape.format(v=version)
            same(rewrite.replace_version(text, version, newer), text,
                 f"{version} was taken out of {text!r}")

    # Nothing to swap is not an excuse to touch the line.
    same(rewrite.replace_version("version: '1.0'", "", "2.0"),
         "version: '1.0'")


@check("a version is replaced everywhere a project spells it out")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        (here / "snap").mkdir()
        (here / "snap/snapcraft.yaml").write_text(
            "name: demo\nversion: '1.4.6'\n")
        (here / "README.md").write_text(
            "demo 1.4.6\n\n    sudo snap install ./demo_1.4.6_amd64.snap\n")
        (here / "untouched.txt").write_text("1.4.6\n")
        changes = rewrite.rewrite_versions(here, "1.4.6", "1.4.7")

        same((here / "snap/snapcraft.yaml").read_text(),
             "name: demo\nversion: '1.4.7'\n")
        assert "1.4.7" in (here / "README.md").read_text()
        # Only files the rewriter knows, and every touched line is reported.
        same((here / "untouched.txt").read_text(), "1.4.6\n")
        same(sorted(c.path for c in changes), ["README.md", "snap/snapcraft.yaml"])
        same(len([line for c in changes for line in c.lines]), 3)


@check("only the anchored source: line is repointed, not the other one")
def _():
    with tempfile.TemporaryDirectory() as home:
        path = Path(home) / "snapcraft.yaml"
        path.write_text(
            "parts:\n  app:\n"
            "    source: https://example.invalid/app-1.0.tar.xz\n"
            "    source-checksum: sha256/" + "0" * 64 + "\n"
            "  launcher:\n    source: snap/local\n")
        rewrite.repoint_yaml(path, r"^(\s*source:\s*).*/app-.*\.tar\.xz\s*$",
                             "https://example.invalid/app-2.0.tar.xz", "f" * 64)
        text = path.read_text()
        assert "app-2.0.tar.xz" in text
        assert "sha256/" + "f" * 64 in text
        # irssi has a second source; repointing it breaks the build.
        assert "source: snap/local" in text


@check("what got built is reported, not what the recipe says")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        snap = db.Snap(name="demo", version="2.0", directory=str(here))
        same(update.built_version(snap), "", "nothing built yet")

        (here / "demo_1.0_amd64.snap").write_bytes(b"old")
        same(update.built_version(snap), "1.0")

        # A failed build leaves recipe and artifact disagreeing.
        assert update.built_version(snap) != snap.version, \
            "a failed build after an update went unnoticed"

        (here / "demo_2.0_amd64.snap").write_bytes(b"new")
        same(update.built_version(snap), "2.0", "the newest one counts")

        # `snap pack --filename` does not have to name an architecture.
        for name in here.glob("*.snap"):
            name.unlink()
        (here / "demo_3.0.snap").write_bytes(b"plain")
        same(update.built_version(snap), "3.0", "no arch in the name")


@check("a missing artifact reads as an update, however current the version")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        snap = db.Snap(name="demo", style="artifact", version="1.0",
                       asset_glob="demo-*.tar.gz", directory=str(here))
        assert update.missing_artifact(snap), "nothing there, so: behind"
        (here / "demo-1.0.tar.gz").write_text("x")
        assert not update.missing_artifact(snap), "it is there now"
        # A project that builds from no file on disk has none to miss.
        same(update.missing_artifact(db.Snap(name="d", directory=str(here))),
             False)
