"""Saying where a snap's releases come from, when it is not a release."""

import contextlib
import io
import tempfile
from pathlib import Path
from types import SimpleNamespace

from snapforge import arch, cli, db, sources
from snapforge.net import NetworkError

from .harness import (check, same, Quiet, _raise, patched, raises,
                      update_state, make_deb)


@check("every upstream in the seed round-trips through configure()")
def _():
    # The spec and the shapes had no way of disagreeing loudly until now.
    import seed
    seeded = [(name, one["upstream"]) for name, one in seed.CONFIG.items()
              if one.get("upstream")]
    assert len(seeded) >= 6, f"only {len(seeded)} to check against"
    for name, config in seeded:
        values = {k: v for k, v in config.items() if k != "kind"}
        same(sources.configure(config["kind"], values), config, name)


@check("a setting the shape does not have is refused, not written down")
def _():
    for kind, values, wanted in (
            ("apt", {"package": "x"}, "needs base"),
            ("apt", {"base": "u", "package": "x", "wibble": "1"},
             "has no 'wibble'"),
            ("index", {"url": "u", "pattern": "(a)", "asset": "a",
                       "sha": "x"}, "has no 'sha'"),
            ("nonsense", {}, "no such upstream kind"),
            ("", {}, "no such upstream kind")):
        try:
            sources.configure(kind, values)
            assert False, f"{kind} {values} should have been refused"
        except sources.BadUpstream as exc:
            assert wanted in str(exc), f"{kind}: {exc}"


@check("a regex that cannot say which part is the version is refused")
def _():
    # findall with two groups gives tuples, and newest() sorts them.
    for pattern, wanted in (("emacs-(", "not a regular expression"),
                            ("(a)(b)", "2 capturing groups"),
                            ("no-group-here", "0 capturing groups")):
        try:
            sources.configure("index", {"url": "u", "pattern": pattern,
                                        "asset": "a"})
            assert False, f"{pattern!r} should have been refused"
        except sources.BadUpstream as exc:
            assert wanted in str(exc), f"{pattern}: {exc}"


@check("a placeholder the shape cannot fill in is caught before it is used")
def _():
    # _index passes version alone, so {tag} was a KeyError a year later.
    try:
        sources.configure("index", {"url": "u", "pattern": "(a)",
                                    "asset": "a-{tag}.tar.xz"})
        assert False, "should have been refused"
    except sources.BadUpstream as exc:
        assert "cannot fill in" in str(exc), str(exc)
    # tag-archive does fill it in, so the same asset is fine there.
    made = sources.configure("tag-archive",
                             {"repo": "a/b", "asset": "a-{tag}.tar.xz",
                              "download": "http://x/{tag}"})
    same(made["asset"], "a-{tag}.tar.xz")


@check("the apt index is worked out from the repository root")
def _():
    made = sources.configure("apt", {"base": "https://x/apt",
                                     "package": "thing"})
    # {arch} is left standing: the record must work on any machine.
    same(made["index"],
         "https://x/apt/dists/stable/main/binary-{arch}/Packages")
    same(sources._fill(made["index"], base="https://x/apt"),
         f"https://x/apt/dists/stable/main/binary-{arch.host()}/Packages")
    # Named outright, it wins: signal's is under xenial, not stable.
    named = sources.configure("apt", {"base": "https://x/apt",
                                      "package": "thing",
                                      "index": "https://x/apt/other"})
    same(named["index"], "https://x/apt/other")


@check("settings are given as name=value, and anything else says so")
def _():
    same(sources.parse_pairs(["a=1", "b=x=y", "c="]),
         {"a": "1", "b": "x=y", "c": ""})
    try:
        sources.parse_pairs(["glob"])
        assert False, "should have been refused"
    except sources.BadUpstream as exc:
        assert "is not key=value" in str(exc), str(exc)


@check("what configure() builds is what resolve() can read")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        make_deb(here / "thing_2.4_amd64.deb", package="thing", version="2.4")
        config = sources.configure("local", {"glob": "thing_*_amd64.deb"})
        release = sources.resolve(config, directory=here)
        same(release.version, "2.4")
        same(release.asset, "thing_2.4_amd64.deb")


@check("an upstream that does not resolve leaves the record as it was")
def _():
    # Written down unresolved, a wrong regex reads as "up to date" for ever.
    from snapforge import update
    was = {"kind": "local", "glob": "was_*.deb"}
    snap = db.Snap(name="demo", version="1.0", upstream=dict(was))
    wanted = sources.configure("apt", {"base": "https://x/apt",
                                       "package": "thing"})
    with patched(update, resolve=_raise(NetworkError("HTTP 404"))):
        with raises(NetworkError, "it should have refused"):
            update.retrack(snap, wanted)
        same(snap.upstream, was, "the new upstream was kept anyway")
        # Forced, it is written down and the caller is told of no release.
        same(update.retrack(snap, wanted, force=True), None)
        same(snap.upstream, wanted)


@check("folder and local name the same shape, wherever they are typed")
def _():
    same(sources.configure("folder", {"glob": "d-*.deb"}),
         sources.configure("local", {"glob": "d-*.deb"}))
    same(sources.configure("folder", {})["kind"], "local")


@check("both front ends refuse an upstream the same way")
def _():
    # The terminal had its own copy of the rollback, and the dashboard
    # had none; both go through update.track now.
    from snapforge import update
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home))
        snap = db.Snap(name="demo", version="1.0",
                       upstream={"kind": "local", "glob": "was_*.deb"})
        store.add(snap)
        words = ["track", "demo", "apt", "base=https://x/apt",
                 "package=thing"]
        wanted = sources.configure("apt", {"base": "https://x/apt",
                                           "package": "thing"})
        with patched(update, resolve=_raise(NetworkError("HTTP 404"))):
            with raises(SystemExit, "it should have refused"):
                cli.cmd_track(store, cli.parse_args(words), Quiet())
            same(db.Database(Path(home)).get("demo").upstream,
                 {"kind": "local", "glob": "was_*.deb"},
                 "the refusal did not reach the record")

            forced = cli.parse_args([*words, "--force"])
            same(cli.cmd_track(store, forced, Quiet()), 0)
            same(db.Database(Path(home)).get("demo").upstream, wanted)


@check("what a record still needs for its new upstream is said once")
def _():
    from snapforge import update
    release = sources.Release(version="4200",
                              asset="sublime-text_build-4200_amd64.deb")
    artifact = db.Snap(name="demo", style="artifact")
    notes = update.fitting(artifact, release)
    assert any("sublime-text_build-*_amd64.deb" in n for n in notes), notes
    # With a glob on the record there is nothing to say.
    artifact.asset_glob = "sublime-text_build-*_amd64.deb"
    same(update.fitting(artifact, release), [])

    recipe = db.Snap(name="demo", style="recipe")
    recipe.snapcraft_yaml = "parts:\n  a:\n    source: x\n  b:\n    source: y\n"
    assert any("source_anchor" in n for n in update.fitting(recipe, release))
    recipe.source_anchor = r"^(\s*source:\s*)x$"
    same(update.fitting(recipe, release), [])


@check("a release with no one file in it is still asked what a record needs")
def _():
    from snapforge import github, update
    # `track ... repo` hands fitting() a release, not one file in one.
    release = github.Release(repo="a/b", tag="v2.0", version="2.0")
    artifact = db.Snap(name="demo", style="artifact",
                       asset="demo_2.0_amd64.deb")
    notes = update.fitting(artifact, release)
    assert any("demo_*_amd64.deb" in n for n in notes), notes
    artifact.asset_glob = "demo_*_amd64.deb"
    same(update.fitting(artifact, release), [])


@check("track none stops a snap being checked against anything")
def _():
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home))
        snap = db.Snap(name="demo", repo="a/b", version="1.0",
                       asset_pattern="^x$", upstream={"kind": "local"})
        store.add(snap)
        same(cli.cmd_track(store, cli.parse_args(["track", "demo", "none"]),
                           Quiet()), 0)
        back = db.Database(Path(home)).get("demo")
        same((back.upstream, back.repo, back.asset_pattern), ({}, "", ""))
        same(update_state(back), "untracked")


@check("the track command itself routes what it is given")
def _():
    # settle() and untrack() were tested; nothing reached them through argv.
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home))
        project_dir = Path(home) / "proj"
        project_dir.mkdir()
        make_deb(project_dir / "thing_2.4_amd64.deb", package="thing",
                 version="2.4")
        store.add(db.Snap(name="thing", style="artifact", kind="deb",
                          version="0.1", directory=str(project_dir)))

        def track(*words):
            return cli.cmd_track(store, cli.parse_args(["track", *words]),
                                 Quiet())

        # `folder` is the word the rest of the tool uses for kind `local`.
        same(track("thing", "folder", "glob=thing_*_amd64.deb"), 0)
        same(store.get("thing").upstream,
             {"kind": "local", "glob": "thing_*_amd64.deb"})

        same(track("thing", "none"), 0)
        same(store.get("thing").upstream, {})

        # A name that is not registered, and a name that is missing.
        for words, _ in ((("nothing-like-this",), "nothing registered"),
                              ((), "track needs a name")):
            with raises(SystemExit, f"{words} should have exited"):
                track(*words)


@check("an option between positionals is read, not an unrecognized argument")
def _():
    # `db pull --dir x btop` stopped at btop with argparse.parse_args.
    from snapforge import cli
    for argv, want in ((["db", "pull", "--dir", "x", "btop"],
                        ("db", ["pull", "btop"])),
                       (["update", "foo", "--force", "bar"],
                        ("update", ["foo", "bar"])),
                       (["create", "--name", "foo", "o/r"], ("create", ["o/r"])),
                       (["create", "o/r", "--name", "foo"], ("create", ["o/r"])),
                       ([], ("", []))):
        args = cli.parse_args(argv)
        same((args.command, args.rest), want, argv)


@check("create with a name already held stops before the project is written")
def _():
    # The project was written first, on top of the one the name belonged to.
    from snapforge import cli, db, project
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        store.add(db.Snap(name="bat", repo="sharkdp/bat", version="1.0"))
        made = SimpleNamespace(name="bat", origin=SimpleNamespace(repo="someone/bat"))
        written = []
        with patched(project, create=lambda *a, **k: written.append(a)), \
                raises(SystemExit, "it went ahead"):
            cli._finish_create(store, SimpleNamespace(directory=None), None,
                               made, "someone/bat")
        same(written, [], "the project was written anyway")


@check("every command is in the usage text, so none is reachable but unlisted")
def _():
    import re as regex
    from snapforge import cli

    listed = set(regex.findall(r"^  snapkit (\S+)", cli.USAGE, regex.M))
    for name in cli.COMMANDS:
        # "" is the dashboard, and an alias is the same command twice.
        if not name or name in cli.ALIASES:
            continue
        assert name in listed, f"`snapkit {name}` runs and the usage text omits it"
    for name in cli.ALIASES:
        assert name in cli.COMMANDS, f"{name} is listed as an alias of nothing"


@check("remove off a pipe leaves the snap alone rather than raising")
def _():
    import builtins
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home))
        store.add(db.Snap(name="demo", repo="a/b", version="1.0"))
        args = cli.parse_args(["remove", "demo"])
        with patched(builtins, input=_raise(EOFError())):
            same(cli.cmd_remove(store, args, Quiet()), 1)
        assert "demo" in store, "an unanswered question removed it anyway"


@check("track kinds prints every kind, so none is reachable but unlisted")
def _():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        same(cli.cmd_track(None, cli.parse_args(["track", "kinds"]), Quiet()), 0)
    printed = buffer.getvalue()
    for kind in sources.RESOLVERS:
        assert f"  {kind}  --  " in printed, f"{kind} is not in the list"
    for shape in sources.SPECS:
        for key in shape.required:
            assert key in printed, f"{shape.kind}: {key} is not printed"


@check("every shape says what it takes, so `track kinds` cannot go stale")
def _():
    same(sorted(sources.SPEC), sorted(sources.RESOLVERS),
         "a shape without a spec is one `track` cannot reach")
    for shape in sources.SPECS:
        assert shape.summary and shape.example, shape.kind
        assert shape.example.startswith("snapkit track "), shape.kind
        for key in shape.required:
            assert key in shape.keys, f"{shape.kind}: {key} is undescribed"
        for key in shape.templates:
            assert key in shape.keys or key in sources.COMMON, \
                f"{shape.kind}: {key} is templated but not a setting"
