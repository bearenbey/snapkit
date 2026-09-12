"""Asking whether a snap is behind, and what to build the new one from."""

import io
import tarfile
import tempfile
from pathlib import Path

from snapforge import db, project, update

from .harness import check, same, Quiet, patched, raises, make_deb


@check("a matching version is up to date even with no tag recorded")
def _():
    # Comparing both made every imported project read as out of date.
    class Asset:
        def __init__(self, name):
            self.name, self.url = name, "http://x/" + name

    class Release:
        version, tag = "1.4.7", "v1.4.7"
        assets = [Asset("demo-1.4.7-x86_64-linux.tar.gz")]

    with patched(project.github, release=lambda repo, tag=None: Release()):
        no_tag = db.Snap(name="demo", repo="a/b", kind="archive",
                         version="1.4.7", tag="",
                         asset_pattern=r"^demo\-.*\.tar\.gz$")
        release, asset, note = update.check(no_tag)
        same(asset, None, "an up-to-date snap was reported as behind")
        behind = db.Snap(name="demo", repo="a/b", kind="archive",
                         version="1.4.6", tag="",
                         asset_pattern=r"^demo\-.*\.tar\.gz$")
        release, asset, note = update.check(behind)
        assert asset is not None, "a genuinely behind snap was missed"


@check("a .deb's own Version: differing from its tag is not an update")
def _():
    # release.version comes from the tag, snap.version from the control file.
    from snapforge import sources
    class Release:
        version, tag = "1.2.3", "v1.2.3"
    snap = db.Snap(name="demo", repo="a/b", kind="deb",
                   version="1.2.3-1", tag="v1.2.3")
    assert update._settled(snap, Release(), False), "reported behind"
    snap.tag = "v1.2.2"
    assert not update._settled(snap, Release(), False), "a new tag was missed"
    # a folder has no tag on either side, so the version is what there is
    found = sources.Release(version="1.2.3", asset="a", url="u", tag="")
    no_tag = db.Snap(name="demo", kind="deb", version="1.2.3", tag="")
    assert update._settled(no_tag, found, False)
    no_tag.version = "1.2.2"
    assert not update._settled(no_tag, found, False)


@check("a file with no version reads as 0 at check time, as it did at create")
def _():
    # create wrote "0"; check read ""; every check said update, and the
    # update then wrote version: '' into the recipe.
    from snapforge import sources
    with tempfile.TemporaryDirectory() as here:
        here = Path(here)
        (here / "tool-linux-x86_64.tar.gz").write_bytes(b"x")
        found = sources.resolve({"kind": "local", "glob": "*.tar.gz"},
                                directory=here)
        same(found.version, "0")


@check("a verifier is given the download's url, not asked the release for one")
def _():
    # A GitHub release has no url of its own, and the tar-member check
    # read one off it anyway.
    from snapforge import github, sources
    with tempfile.TemporaryDirectory() as here:
        path = Path(here) / "a.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            info = tarfile.TarInfo("app-1.0/configure")
            tar.addfile(info, io.BytesIO(b""))
        release = github.Release(repo="a/b", tag="v1.0", version="1.0")
        said = sources.verify({"kind": "tar-member", "member": "app-{version}/configure"},
                              path, release, "https://h/a.tar.gz")
        assert "configure" in said, said
        try:
            sources.verify({"kind": "tar-member", "member": "nope"},
                           path, release, "https://h/a.tar.gz")
            assert False, "a missing member passed"
        except sources.NetworkError as exc:
            assert "https://h/a.tar.gz" in str(exc), exc


@check("a snap with nothing to match against is not checked at all")
def _():
    # A guessed upstream stays inert: Signal's .deb is not on GitHub at all.
    snap = db.Snap(name="signal-desktop", repo="signalapp/Signal-Desktop",
                   kind="deb", version="8.24.1", asset_pattern="")
    with raises(update.NotTracked, "it went upstream anyway"):
        update.check(snap)


@check("a check that cannot run comes back as an answer, not an exception")
def _():
    # Three callers phrased the same finding, and two had already drifted.
    nothing = db.Snap(name="demo")
    found = update.situation(nothing)
    same(found.state, "untracked")
    same(found.words, update.STATES["untracked"])
    same(found.behind, False)
    same(found.latest, "")
    assert found.problem, "it should say why"


@check("an upstream that is not a repository is still checked")
def _():
    # Skipping on `repo` silently stopped checking every non-GitHub project.
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        make_deb(here / "demo_1.0_amd64.deb", version="1.0")
        snap = db.Snap(name="demo", style="artifact", version="1.0",
                       kind="deb", asset="demo_1.0_amd64.deb",
                       asset_glob="demo_*_amd64.deb", directory=str(here),
                       upstream={"kind": "local", "glob": "demo_*_amd64.deb"})
        same(snap.repo, "", "this is the case that used to be skipped")
        same(update.situation(snap).state, "current")

        make_deb(here / "demo_2.0_amd64.deb", version="2.0")
        found = update.situation(snap)
        same(found.state, "behind")
        same(found.latest, "2.0")


@check("the superseded file is cleaned however the upstream is described")
def _():
    # discord's config names no glob, so its old .deb was never removed.
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        (here / "snap").mkdir()
        (here / "snap/snapcraft.yaml").write_text("name: demo\nversion: '1.0'\n")
        make_deb(here / "demo_1.0_amd64.deb", version="1.0")
        make_deb(here / "demo_2.0_amd64.deb", version="2.0")
        snap = db.Snap(name="demo", style="artifact", version="1.0",
                       kind="deb", asset="demo_1.0_amd64.deb",
                       asset_glob="demo_*_amd64.deb", directory=str(here),
                       # No `glob` here on purpose: the record has it.
                       upstream={"kind": "local"})
        release, asset, _note = update.check(snap)
        same(asset.glob, "demo_*_amd64.deb", "the record's glob was dropped")

        update.update(snap, release, asset, Quiet())
        assert not (here / "demo_1.0_amd64.deb").exists(), \
            "the superseded file is still there"
        assert (here / "demo_2.0_amd64.deb").is_file()


@check("check() reports a rename instead of failing on it")
def _():
    class Asset:
        def __init__(self, name):
            self.name, self.url = name, "http://x/" + name

    class Release:
        version, tag = "2.0", "v2.0"
        assets = [Asset("demo-2.0-x86_64-linux.tar.gz")]
    snap = db.Snap(name="demo", repo="a/b", kind="archive", version="1.0",
                   tag="v1.0", asset="demo-1.0-x86_64-linux.tbz",
                   asset_pattern=r"^demo\-[0-9][0-9A-Za-z.+~_-]*\-x86_64\-linux\.tbz$")
    with patched(project.github, release=lambda repo, tag=None: Release()):
        release, asset, note = update.check(snap)
        same(asset.name, "demo-2.0-x86_64-linux.tar.gz")
        assert "no longer publishes" in note, note
