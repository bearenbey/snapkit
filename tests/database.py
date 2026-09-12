"""The shared recipe database: what goes into it, and what comes back."""

import json
import tempfile
from pathlib import Path

from snapforge import db, snapdb

from .harness import check, same, Quiet, patched, env, raises


def a_project(root, name, extra=None):
    """A project directory with a recipe and whatever else is asked for."""
    directory = root / f"{name}-snap"
    (directory / "snap").mkdir(parents=True)
    (directory / "snap" / "snapcraft.yaml").write_text(
        f"name: {name}\nversion: '1.0'\nsummary: a thing\n"
        f"confinement: strict\nbase: core24\n"
        f"parts:\n  {name}:\n    plugin: dump\n"
        f"    source: {name}-1.0.tar.gz\n")
    (directory / f"{name}-1.0.tar.gz").write_bytes(b"not really a tarball")
    (directory / f"{name}_1.0_amd64.snap").write_bytes(b"a built snap")
    for relative, body in (extra or {}).items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return directory


@check("an index key that is not a snap name writes nothing")
def _():
    # The key became a directory name, and "../x" is a fine key.
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        found = {"snaps": {"../escape": {"files": {}}}}
        with raises(snapdb.DatabaseError, "it was written"):
            snapdb.fetch("../escape", root / "here", found, url="file:///x")
        assert not (root / "escape").exists()


@check("a build's leavings stay out of the database")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        directory = a_project(root, "demo")
        (directory / "prime").mkdir()
        (directory / "prime" / "junk").write_text("x")
        (directory / "__pycache__").mkdir()
        (directory / "__pycache__" / "x.pyc").write_text("x")

        kept, _ = snapdb.project_files(directory)
        names = {str(k) for k in kept}
        same("snap/snapcraft.yaml" in names, True)
        # The release, the built snap and the build tree are not packaging.
        same(any(n.endswith(".tar.gz") for n in names), False)
        same(any(n.endswith(".snap") for n in names), False)
        same(any(n.startswith("prime/") for n in names), False)
        same(any("__pycache__" in n for n in names), False)


@check("a recipe naming a file the database will not carry is called out")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        directory = a_project(root, "demo")
        (directory / "vendor").mkdir()
        (directory / "vendor" / "big.tar.xz").write_bytes(b"vendored")
        recipe = directory / "snap" / "snapcraft.yaml"
        recipe.write_text(recipe.read_text() +
                          "  extra:\n    plugin: nil\n"
                          "    source: vendor/big.tar.xz\n")

        kept, _ = snapdb.project_files(directory)
        # Excluded as payload, so only asking the recipe finds this.
        unmet = snapdb.unmet_sources(directory, kept, artifact="demo-1.0.tar.gz")
        same(unmet, ["vendor/big.tar.xz"])

        # The one release the project downloads is not a gap.
        same(snapdb.unmet_sources(directory, kept, artifact="demo-1.0.tar.gz")
             .count("demo-1.0.tar.gz"), 0)


@check("a file saved here under another name is not called a missing source")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        directory = a_project(root, "unityhub")
        recipe = directory / "snap" / "snapcraft.yaml"
        # Saved here under another name, which the recipe uses.
        recipe.write_text(recipe.read_text().replace(
            "source: unityhub-1.0.tar.gz", "source: UnityHubSetup.deb"))
        kept, _ = snapdb.project_files(directory)
        upstream, here = "unityhub_3.21.1_amd64.deb", "UnityHubSetup.deb"
        same(snapdb.unmet_sources(directory, kept, upstream),
             ["UnityHubSetup.deb"], "the upstream name alone cannot match")
        same(snapdb.unmet_sources(directory, kept, (upstream, here)), [],
             "the local name is what the recipe says, so nothing is unmet")


@check("what is published is what comes back, mode and all")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        directory = a_project(root, "demo", {"overlay/bin/launcher": "#!/bin/sh\n"})
        (directory / "overlay" / "bin" / "launcher").chmod(0o755)

        store = db.Database(root / "register.json")
        snap = db.Snap(name="demo", version="1.0", directory=str(directory),
                       asset="demo-1.0.tar.gz", asset_glob="demo-*.tar.gz",
                       style="artifact", repo="who/demo")
        store.add(snap)

        published = root / "snap-db"
        index, left_out = snapdb.publish([snap], published)
        same(left_out, {})
        same(sorted(index["snaps"]["demo"]["files"]),
             ["overlay/bin/launcher", "snap/snapcraft.yaml"])

        url = published.resolve().as_uri()
        same(snapdb.index(url)["snaps"]["demo"]["version"], "1.0")

        back = root / "back"
        snapdb.fetch("demo", back, url=url)
        same((back / "snap" / "snapcraft.yaml").read_text(),
             (directory / "snap" / "snapcraft.yaml").read_text())
        # A launcher that arrives without its exec bit will not run.
        same(bool((back / "overlay" / "bin" / "launcher").stat().st_mode & 0o111),
             True)


@check("a project pulled from the database arrives whole, icon and record")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        directory = a_project(root, "demo", {"snap/gui/demo.png": "PNG"})
        snap = db.Snap(name="demo", version="1.0", directory=str(directory),
                       asset="demo-1.0.tar.gz", asset_glob="demo-*.tar.gz",
                       style="artifact", repo="who/demo")
        published = root / "snap-db"
        snapdb.publish([snap], published)
        url = published.resolve().as_uri()

        # `install` and the dashboard's `g` are this one call now.
        back = root / "back"
        pulled, recipe, is_snapcraft = snapdb.install(
            "demo", back, url=url, store=root / "register")
        same(is_snapcraft, True)
        same(recipe, back / "snap" / "snapcraft.yaml")
        # The record: what reading the project alone can never say.
        same((pulled.style, pulled.asset_glob, pulled.repo),
             ("artifact", "demo-*.tar.gz", "who/demo"))
        # And the icon, which one of the two front ends used to drop.
        same(pulled.icon, "snap/gui/demo.png")
        assert (back / "snap" / "gui" / "demo.png").is_file(), "icon not fetched"
        # Beside the register it is going into, not beside the default one.
        assert (root / "register" / "icons" / "demo.png").is_file(), \
            "the kept icon did not land in this register"


@check("db pull registers what it writes, so it can be built by name")
def _():
    from snapforge import cli
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        directory = a_project(root, "demo")
        snap = db.Snap(name="demo", version="1.0", directory=str(directory),
                       asset="demo-1.0.tar.gz", asset_glob="demo-*.tar.gz",
                       style="artifact", repo="who/demo")
        published = root / "snap-db"
        index, _ = snapdb.publish([snap], published)

        store = db.Database(root / "register")
        out = root / "out"
        with env("SNAPKIT_DB_URL", published.resolve().as_uri()):
            args = cli.parse_args(["db", "pull", "demo", "--dir", str(out)])
            same(cli.db_pull(store, args, ["demo"], index, Quiet()), 0)
        assert (out / "demo-snap" / "snap" / "snapcraft.yaml").is_file()
        # And not only files: the register knows it, where it is.
        pulled = db.Database(root / "register").get("demo")
        same(Path(pulled.directory), (out / "demo-snap").resolve())
        same((pulled.style, pulled.repo), ("artifact", "who/demo"))


@check("a database naming a file outside the project writes nothing at all")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        directory = a_project(root, "demo")
        snap = db.Snap(name="demo", version="1.0", directory=str(directory),
                       asset="demo-1.0.tar.gz", style="artifact")
        published = root / "snap-db"
        snapdb.publish([snap], published)

        # `into / relative` follows an absolute path or a ../ right out.
        index = json.loads((published / "index.json").read_text())
        index["snaps"]["demo"]["files"]["../../pwned"] = {
            "sha256": "0" * 64, "exec": True}
        (published / "index.json").write_text(json.dumps(index))

        target = root / "pull" / "demo"
        try:
            snapdb.fetch("demo", target, url=published.resolve().as_uri())
            assert False, "a path outside the project was accepted"
        except snapdb.DatabaseError as exc:
            assert "outside the project" in str(exc), str(exc)
        assert not (root / "pull" / "pwned").exists(), "it escaped anyway"
        assert not (root / "pwned").exists(), "it escaped anyway"
        # Refused before anything is written, not halfway through.
        assert not any(target.rglob("*")) if target.exists() else True, \
            "half a project was written before the refusal"


@check("a shell command cannot arrive in a record off the network")
def _():
    # build_with runs through a shell, so the index may not set it.
    assert "build_with" not in snapdb.RECORD, \
        "build_with is back in RECORD, and the index can run shell again"
    snap = db.Snap(name="demo")
    snapdb.apply_record(snap, {"record": {"build_with": "rm -rf ~",
                                          "pack": "pack.py"}})
    same(snap.build_with, "", "the index set build_with")
    same(snap.pack, "pack.py", "pack.py is the supported way, and stays")


@check("the index carries what a pulled project needs to update itself")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        directory = a_project(root, "demo")
        snap = db.Snap(name="demo", version="1.0", directory=str(directory),
                       asset="demo-1.0.tar.gz", asset_glob="demo-*.tar.gz",
                       style="artifact", repo="who/demo")
        published = root / "snap-db"
        index, _ = snapdb.publish([snap], published)

        # Reading a project never says where its release comes from.
        record = index["snaps"]["demo"]["record"]
        same(record["style"], "artifact")
        same(record["asset_glob"], "demo-*.tar.gz")

        fresh = db.Snap(name="demo")
        snapdb.apply_record(fresh, index["snaps"]["demo"])
        same(fresh.style, "artifact")
        same(fresh.asset_glob, "demo-*.tar.gz")
        same(fresh.repo, "who/demo")


@check("a project with a pack.py says which snapkit it needs, and is refused by one behind")
def _():
    from snapforge import build as buildlib
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        plain = a_project(root, "plain")
        packed = a_project(root, "packed", {"pack.py": "def build(project):\n    pass\n"})
        snaps = [db.Snap(name="plain", version="1.0", directory=str(plain),
                         style="artifact", asset="plain-1.0.tar.gz",
                         asset_glob="plain-*.tar.gz"),
                 db.Snap(name="packed", version="1.0", directory=str(packed),
                         style="artifact", asset="packed-1.0.tar.gz",
                         asset_glob="packed-*.tar.gz", pack="pack.py")]
        published = root / "snap-db"
        index, _ = snapdb.publish(snaps, published)
        assert "needs" not in index["snaps"]["plain"], "a recipe alone needs nothing"
        same(index["snaps"]["packed"]["needs"], buildlib.NEEDS)

        # This snapkit is new enough for what it just published.
        same(snapdb.needs_newer(index["snaps"]["packed"]), "")
        url = published.resolve().as_uri()
        snapdb.fetch("packed", root / "out", index, url)
        assert (root / "out" / "pack.py").is_file()

        # One behind is told at pull time, by name, and writes nothing.
        with patched(snapdb, __version__="0.2.0"):
            same(snapdb.needs_newer(index["snaps"]["packed"]), buildlib.NEEDS)
            try:
                snapdb.fetch("packed", root / "old", index, url)
                assert False, "an old snapkit pulled a new pack.py"
            except snapdb.DatabaseError as exc:
                assert "upgrade snapkit" in str(exc) and buildlib.NEEDS in str(exc), str(exc)
            assert not (root / "old").exists(), "something was written first"
            # and the recipe-only project still comes through
            snapdb.fetch("plain", root / "old-plain", index, url)


@check("the version is spelled the same in the package, pyproject and the recipe")
def _():
    import snapforge
    # The project root: this package sits one level under it.
    here = Path(__file__).resolve().parent.parent
    pyproject = (here / "pyproject.toml").read_text()
    recipe = (here / "snap" / "snapcraft.yaml").read_text()
    assert f'version = "{snapforge.__version__}"' in pyproject, snapforge.__version__
    assert f"version: '{snapforge.__version__}'" in recipe, snapforge.__version__
    from snapforge import build as buildlib, versions
    assert versions.version_key(buildlib.NEEDS) <= versions.version_key(snapforge.__version__), (
        "NEEDS names a snapkit that does not exist yet")


@check("a snap the database does not have says what it does have")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        snap = db.Snap(name="freetube", version="1.0",
                       directory=str(a_project(root, "freetube")))
        published = root / "snap-db"
        snapdb.publish([snap], published)
        url = published.resolve().as_uri()
        try:
            snapdb.fetch("freetub", root / "back", url=url)
            assert False, "should have raised"
        except snapdb.DatabaseError as exc:
            assert "freetube" in str(exc), str(exc)


@check("a project that has moved on since publishing is spotted")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        directory = a_project(root, "demo")
        snap = db.Snap(name="demo", version="1.0", directory=str(directory))
        published = root / "snap-db"
        index, _ = snapdb.publish([snap], published)

        same(snapdb.local_fingerprint(directory),
             index["snaps"]["demo"]["fingerprint"])

        # An update edits the recipe, and the database never hears of it.
        recipe = directory / "snap" / "snapcraft.yaml"
        recipe.write_text(recipe.read_text().replace("1.0", "1.1"))
        assert snapdb.local_fingerprint(directory) != \
            index["snaps"]["demo"]["fingerprint"], "drift went unnoticed"


@check("publishing writes the page that says what the database is")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        snap = db.Snap(name="demo", version="1.0",
                       directory=str(a_project(root, "demo")))
        published = root / "snap-db"
        snapdb.publish([snap], published)
        readme = (published / "README.md").read_text()
        same(readme.startswith("# snap-db"), True)
        # Hand-written once and lost on the next publish, twice.
        same("snapkit db pull" in readme, True)


@check("a database written by a newer snapkit is refused, not misread")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        published = root / "snap-db"
        published.mkdir()
        (published / "index.json").write_text(
            json.dumps({"schema": snapdb.SCHEMA + 1, "snaps": {}}))
        try:
            snapdb.index(published.resolve().as_uri())
            assert False, "should have raised"
        except snapdb.DatabaseError as exc:
            assert "upgrade snapkit" in str(exc), str(exc)
