"""The register: one file per snap, the recipe beside it, and migration."""

import json
import tempfile
import time
from pathlib import Path

from snapforge import db

from .harness import check, same, raises


@check("every annotation resolves, on a Python that evaluates them eagerly")
def _():
    # 3.14 defers annotations, so 3.13 and earlier raise NameError instead.
    import importlib
    import pkgutil
    import typing

    import snapforge

    for found in pkgutil.iter_modules(snapforge.__path__):
        module = importlib.import_module(f"snapforge.{found.name}")
        for thing in vars(module).values():
            if isinstance(thing, type) and thing.__module__ == module.__name__:
                typing.get_type_hints(thing)


@check("the register survives a round trip, and delete takes the recipe")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        first = db.Database(root)
        first.add(db.Snap(name="demo", repo="a/b", kind="deb",
                          version="1.0", recipe_text="name: demo\n"))
        same(len(db.Database(root)), 1, "reload")
        same(db.Database(root).get("demo").snapcraft_yaml, "name: demo\n")
        same(db.Database(root).find_repo("A/B").name, "demo", "case-insensitive")
        assert db.Database(root).find_repo("nope/nope") is None

        # the record and the recipe are their own files
        record = root / "snaps" / "demo.json"
        recipe = root / "recipes" / "demo.yaml"
        assert record.is_file() and recipe.is_file(), "not split into files"
        assert "snapcraft_yaml" not in record.read_text(), \
            "the recipe is still inline in the record"
        same(recipe.read_text(), "name: demo\n")

        db.Database(root).remove("demo")
        same(len(db.Database(root)), 0, "after remove")
        assert not record.exists(), "the record outlived the removal"
        assert not recipe.exists(), "the recipe outlived the record"


@check("a recipe is not read until something asks for it")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        store = db.Database(root)
        store.add(db.Snap(name="demo", repo="a/b", recipe_text="name: demo\n"))
        fresh = db.Database(root)
        same(fresh.get("demo").recipe_text, None,
             "the recipe was read during load")
        same(fresh.get("demo").snapcraft_yaml, "name: demo\n",
             "asking for it did not read it")
        same(fresh.get("demo").recipe_text, "name: demo\n", "it was not kept")


@check("two registers do not read each other's recipes")
def _():
    with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
        first = db.Database(Path(one))
        first.add(db.Snap(name="demo", repo="a/b", recipe_text="from one\n"))
        second = db.Database(Path(two))
        second.add(db.Snap(name="demo", repo="a/b", recipe_text="from two\n"))
        same(db.Database(Path(one)).get("demo").snapcraft_yaml, "from one\n")
        same(db.Database(Path(two)).get("demo").snapcraft_yaml, "from two\n")


@check("an emptied recipe does not come back from the dead")
def _():
    # An emptied recipe left its file behind, so the next load read it back.
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        store = db.Database(root)
        store.add(db.Snap(name="demo", repo="a/b", recipe_text="name: demo\n"))
        snap = store.get("demo")
        snap.snapcraft_yaml = ""
        store.add(snap)
        assert not (root / "recipes" / "demo.yaml").exists(), \
            "the recipe file outlived the recipe"
        same(db.Database(root).get("demo").snapcraft_yaml, "",
             "the old recipe came back")

        # and a record whose recipe was never read keeps the one on disk
        store.add(db.Snap(name="keep", repo="c/d", recipe_text="kept\n"))
        untouched = db.Database(root).get("keep")
        same(untouched.recipe_text, None, "it was read during load")
        db.Database(root).add(untouched)
        same(db.Database(root).get("keep").snapcraft_yaml, "kept\n",
             "an unread recipe was wiped by a write")


@check("a record renamed by hand does not become two records")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        store = db.Database(root)
        store.add(db.Snap(name="demo", repo="a/b", recipe_text="x\n"))
        (root / "snaps" / "demo.json").rename(root / "snaps" / "renamed.json")

        fresh = db.Database(root)
        same(fresh.names(), ["demo"], "the renamed file was not read")
        fresh.add(fresh.get("demo"))
        same(sorted(p.name for p in (root / "snaps").glob("*.json")),
             ["demo.json"], "writing it left the old file behind")


@check("one unreadable record does not take the register with it")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        store = db.Database(root)
        for index in range(5):
            store.add(db.Snap(name=f"s{index}", repo=f"a/b{index}"))
        (root / "snaps" / "s2.json").write_text("{not json")

        fresh = db.Database(root)
        same(fresh.names(), ["s0", "s1", "s3", "s4"], "the good ones were lost")
        same(len(fresh.problems), 1, "the bad one was not reported")
        same(fresh.problems[0][0].name, "s2.json")
        # and one that parses but names no snap lands beside it
        (root / "snaps" / "s3.json").write_text('{"repo": "a/b"}')
        fresh = db.Database(root)
        same(len(fresh.problems), 2, "the nameless one was not reported")
        assert "s3" not in fresh.snaps


@check("a record does not grow without bound as it is rebuilt")
def _():
    snap = db.Snap(name="x")
    for index in range(300):
        snap.record_build(f"1.0.{index}")
    same(len(snap.history), db.HISTORY_KEPT, "history was not trimmed")
    same(snap.builds, 300, "the count of builds was lost with the detail")
    same(snap.history[-1]["version"], "1.0.299", "the newest was trimmed")
    same(snap.version, "1.0.299")


@check("a register from the single-file days is migrated, not lost")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        (root / "snapkit.json").write_text(json.dumps({
            "schema": 1,
            "snaps": {
                "btop": {"name": "btop", "repo": "aristocratos/btop",
                         "version": "1.4.7", "kind": "archive",
                         "snapcraft_yaml": "name: btop\nversion: '1.4.7'\n"},
                "bat": {"name": "bat", "repo": "sharkdp/bat",
                        "version": "0.26.1", "snapcraft_yaml": "name: bat\n"},
            }}, indent=2))

        store = db.Database(root)
        same(store.names(), ["bat", "btop"], "the snaps did not come across")
        same(store.get("btop").snapcraft_yaml, "name: btop\nversion: '1.4.7'\n",
             "the recipe did not come across")
        same(store.get("btop").repo, "aristocratos/btop")
        assert (root / "snaps" / "btop.json").is_file()
        assert (root / "recipes" / "btop.yaml").is_file()

        # the old file is kept, renamed, so a bad migration can be undone
        assert not (root / "snapkit.json").exists()
        assert (root / "snapkit.json.migrated").is_file(), \
            "the old register was deleted rather than set aside"

        # and it does not run twice
        same(db.Database(root).names(), ["bat", "btop"])


@check("a thousand snaps stay quick to read and cheap to change")
def _():
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        store = db.Database(root)
        recipe = "name: x\n" + ("# padding to a realistic size\n" * 100)
        for index in range(1000):
            store.add(db.Snap(name=f"pkg{index:04d}", repo=f"o/p{index}",
                              version="1.0", summary="a package",
                              recipe_text=recipe))
        same(len(store), 1000)

        start = time.perf_counter()
        reopened = db.Database(root)
        load = time.perf_counter() - start
        same(len(reopened), 1000, "not all of them came back")

        # Best of a few: one sample of a millisecond of disk is noise.
        writes = []
        for _ in range(5):
            start = time.perf_counter()
            reopened.add(reopened.get("pkg0500"))
            writes.append(time.perf_counter() - start)
        write = min(writes)

        # The shape, not the numbers: one snap is not the whole register.
        assert write < load / 4, (
            f"one write took {write*1000:.1f} ms against a {load*1000:.1f} ms "
            f"read -- writes are not staying local")
        # Generous: a shared CI runner is slow, and the shape is the point.
        assert load < 5.0, f"reading 1000 records took {load:.2f} s"
        # and nothing read a recipe to do any of that
        same(reopened.get("pkg0999").recipe_text, None,
             "recipes were read during a load of 1000")


@check("a name held by another repository is refused, not overwritten")
def _():
    # Many repositories are called `bat`, and the second replaced the first.
    with tempfile.TemporaryDirectory() as home:
        path = Path(home) / "snapkit.json"
        store = db.Database(path)
        store.add(db.Snap(name="bat", repo="sharkdp/bat", version="1.0",
                          recipe_text="the original\n"))
        try:
            store.add(db.Snap(name="bat", repo="someone/bat", version="9.9",
                              recipe_text="the impostor\n"))
            assert False, "the collision was allowed"
        except db.NameTaken as exc:
            assert "sharkdp/bat" in str(exc), exc
        kept = db.Database(path).get("bat")
        same(kept.repo, "sharkdp/bat", "the original was replaced")
        same(kept.snapcraft_yaml, "the original\n", "the recipe was replaced")
        # the same repository updating itself is not a collision
        store.add(db.Snap(name="bat", repo="SHARKDP/BAT", version="2.0"))
        same(db.Database(path).get("bat").version, "2.0")
        same(store.free_name("bat"), "bat-2")
        same(store.free_name("nothing"), "nothing")
        # and create asks first, so the project on disk is never written over
        store.claim("bat", "sharkdp/bat")          # itself again: fine
        store.claim("nothing")                      # nobody holds it: fine
        for repo in ("someone/bat", ""):            # another repo, or a file
            with raises(db.NameTaken, f"{repo!r} was allowed to take bat"):
                store.claim("bat", repo)
        store.add(db.Snap(name="imported", repo="", version="1.0"))
        with raises(db.NameTaken, "an import with no repo was replaced"):
            store.claim("imported", "someone/imported")


@check("search finds a snap by name, by repository, by summary, by url")
def _():
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        store.add(db.Snap(name="btop", repo="aristocratos/btop",
                          summary="A monitor of resources"))
        store.add(db.Snap(name="bat", repo="sharkdp/bat",
                          summary="A cat(1) clone with wings"))
        store.add(db.Snap(name="nvim", repo="neovim/neovim",
                          summary="Vim-fork focused on extensibility"))
        def names(text):
            return [s.name for s in store.search(text)]
        same(names("bat"), ["bat"], "by name")
        same(names("BTOP"), ["btop"], "case does not matter")
        same(names("monitor"), ["btop"], "by summary")
        same(names("aristocratos"), ["btop"], "by repository")
        same(names("https://github.com/neovim/neovim"), ["nvim"], "by url")
        same(names("neovim/neovim"), ["nvim"], "by owner/name")
        same(names("nothing here"), [], "no match")
        same(names(""), [], "empty")
        # Short queries skip prose: "b" is in nvim's "extensibility".
        same(sorted(names("b")), ["bat", "btop"], "short query hit a summary")
        same(names("ext"), ["nvim"], "three characters do search summaries")


@check("a broken register is reported, not silently emptied")
def _():
    with tempfile.TemporaryDirectory() as home:
        path = Path(home) / "snapkit.json"
        path.write_text("{not json")
        with raises(db.DatabaseError, "should have raised"):
            db.Database(path)
