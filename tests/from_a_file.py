"""Packaging a file on disk, and keeping it in step with its folder."""

import tempfile
from pathlib import Path

from snapforge import classify, db, github, local, project, sources, update
from snapforge.net import NetworkError

from .harness import check, same, Quiet, _raise, patched, raises, make_deb


@check("a zip entry cannot chmod its way out of where it is unpacked")
def _():
    import zipfile
    from snapforge import inspect
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        victim = here / "victim"
        victim.write_text("not executable")
        victim.chmod(0o644)
        archive = here / "payload.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            # ZipFile sanitises what it writes; the exec-bit pass did not.
            entry = zipfile.ZipInfo("../victim")
            entry.external_attr = 0o755 << 16
            zipped.writestr(entry, "x")
        inspect._unpack_archive(archive, here / "out")
        same(bool(victim.stat().st_mode & 0o111), False,
             "a zip entry chmod +x a file outside the unpack directory")


@check("a source release is refused rather than packaged as a snap of source")
def _():
    from snapforge import inspect
    with tempfile.TemporaryDirectory() as home:
        root = Path(home) / "tmux-1.0"
        (root / "etc").mkdir(parents=True)
        # What tmux ships: sources, a configure, and autotools' helpers.
        (root / "configure.ac").write_text("AC_INIT([tmux])\n")
        (root / "Makefile.am").write_text("bin_PROGRAMS = tmux\n")
        (root / "tmux.c").write_text("int main(void){return 0;}\n")
        for helper in ("compile", "install-sh", "missing", "depcomp"):
            script = root / "etc" / helper
            script.write_text("#!/bin/sh\necho no command\n")
            script.chmod(0o755)
        configure = root / "configure"
        configure.write_text("#!/bin/sh\nexit 0\n")
        configure.chmod(0o755)
        # A stray executable script, which is why "nothing built" decides.
        hook = root / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)

        same(inspect.build_system(root), "autotools")
        same(inspect.anything_compiled(root), False)
        same(inspect.source_only(root), "autotools")
        # None of the helpers may be offered as the thing to run.
        same(inspect.find_binaries(root), ["pre-commit"])


@check("a tree with something compiled in it is a build, not a source tree")
def _():
    from snapforge import inspect
    with tempfile.TemporaryDirectory() as home:
        root = Path(home) / "app"
        (root / "bin").mkdir(parents=True)
        # Ships a Makefile, but also a real binary, so it is a build.
        (root / "Makefile").write_text("all:\n")
        binary = root / "bin" / "app"
        binary.write_bytes(b"\x7fELF" + b"\0" * 60)
        binary.chmod(0o755)
        same(inspect.build_system(root), "make")
        same(inspect.anything_compiled(root), True)
        same(inspect.source_only(root), "")


@check("every shape the classifier packages is found on a disk")
def _():
    # Three hand-written lists drifted; .txz and .tbz2 went invisible.
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        suffixes = list(classify.ARCHIVES) + [".deb", ".appimage", ".AppImage"]
        for suffix in suffixes:
            (here / f"demo-1.0{suffix}").write_bytes(b"")
        same(len(classify.packages(here)), len(suffixes),
             "something on disk is not being seen")
        same({p.name for p in classify.packages(here)},
             {f"demo-1.0{s}" for s in suffixes})
        # And what is not a package stays out of it.
        for ignored in ("demo-1.0.rpm", "demo-1.0.tar.gz.sha256", "notes.txt"):
            (here / ignored).write_bytes(b"")
        same(len(classify.packages(here)), len(suffixes))


@check("a folder is searched for what can be packaged, best first")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        make_deb(here / "demo_1.2.3_amd64.deb", version="1.2.3")
        (here / "demo-1.2.3-x86_64.tar.gz").write_bytes(b"")
        (here / "demo-1.2.3.AppImage").write_bytes(b"")
        # Filed out before they are even looked at.
        (here / "demo-1.2.3-arm64.deb").write_bytes(b"")
        (here / "demo-1.2.3.rpm").write_bytes(b"")
        (here / "demo-1.2.3.tar.gz.sha256").write_bytes(b"")

        found = local.find(here)
        same([f.name for f in found],
             ["demo_1.2.3_amd64.deb", "demo-1.2.3-x86_64.tar.gz",
              "demo-1.2.3.AppImage"])
        # The .deb states its version; the others only have their names.
        same(found[0].version, "1.2.3")
        same(found[0].kind, classify.DEB)


@check("a pattern and a glob still find the file one release later")
def _():
    # Both names spelled out: deriving one hides the case worth covering.
    import fnmatch

    class Asset:
        def __init__(self, name):
            self.name = name

    seed = (
        # No version in the name: the pattern is literal, the file replaced.
        ("btop-x86_64-unknown-linux-musl.tbz", "1.4.0",
         "btop-x86_64-unknown-linux-musl.tbz"),
        ("zen.linux-x86_64.tar.xz", "1.21.15b", "zen.linux-x86_64.tar.xz"),
        # And the ones that do.
        ("Godot_v4.3-stable_linux.x86_64.zip", "4.3",
         "Godot_v4.4-stable_linux.x86_64.zip"),
        ("discord-0.0.75.deb", "0.0.75", "discord-0.0.76.deb"),
        ("sublime-text_build-4180_amd64.deb", "4180",
         "sublime-text_build-4181_amd64.deb"),
        ("helium-bin_0.4.11.1_amd64.deb", "0.4.11.1",
         "helium-bin_0.5.0.1_amd64.deb"),
        ("emacs-30.1.tar.xz", "30.1", "emacs-30.2.tar.xz"),
        # Not from the seed: no version it tracks holds an underscore.
        ("app-1-2-3-linux.tar.gz", "1_2_3", "app-1-3-0-linux.tar.gz"),
    )

    for name, version, later in seed:
        pattern = classify.asset_pattern(name, version)
        assert classify.match_pattern([Asset(later)], pattern), \
            f"{pattern} stopped matching at {later}"
        glob = local.glob_for(name, version)
        for one in (name, later):
            assert fnmatch.fnmatch(one, glob), f"{glob} does not match {one}"

    # And neither is so wide that it takes another project's file with it.
    for name, version, _ in seed:
        glob = local.glob_for(name, version)
        for other, _, _ in seed:
            assert other == name or not fnmatch.fnmatch(other, glob), \
                f"{glob} also matches {other}"


@check("the newest of two copies in a folder is the one that counts")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        make_deb(here / "demo_1.9_amd64.deb", version="1.9")
        make_deb(here / "demo_1.10_amd64.deb", version="1.10")
        make_deb(here / "demo_1.11~beta_amd64.deb", version="1.11~beta")
        # Debian ordering: 1.10 above 1.9, and a ~beta below its release.
        same(local.newest(here).version, "1.11~beta")
        make_deb(here / "demo_1.11_amd64.deb", version="1.11")
        same(local.newest(here).version, "1.11")


@check("an Ubuntu build beats a Debian one, and the newer of two beats the older")
def _():
    # None of wezterm's debs names an architecture, so length decided.
    names = ["w-1.Debian10.deb", "w-1.Debian12.deb",
             "w-1.Ubuntu20.04.deb", "w-1.Ubuntu22.04.deb"]
    assets = [github.Asset(name=n, url="") for n in names]
    order = [c.name for c in classify.classify(assets, wanted="w")]
    same(order[0], "w-1.Ubuntu22.04.deb", f"picked {order[0]}")
    # The base is Ubuntu, so every Ubuntu build sorts above every Debian.
    assert order[1] == "w-1.Ubuntu20.04.deb", order
    same(classify.distro_release("w-1.Ubuntu22.04.deb"), 22.04)
    same(classify.distro_release("w-1.tar.gz"), 0.0)


@check("a release that is a crate of a monorepo is looked past")
def _():
    # wezterm's newest tags are vtparse and termwiz, with no build.
    real = "20240203-110809"
    assets = {real: [github.Asset(name="wezterm-1.Ubuntu22.04.deb", url="")]}
    tags = ["vtparse-0.7.0", "termwiz-0.23.3", real]

    def recent_tags(repo, limit=30):
        return tags[:limit]

    def attached(repo, tag):
        return assets.get(tag, [])

    with patched(project.github, recent_tags=recent_tags, assets=attached):
        empty = github.Release(repo="wez/wezterm", tag=tags[0], version="0.7.0")
        found, candidates = project._looking_back("wez/wezterm", empty, Quiet())
    same(found.tag, real, "did not reach the release with a build on it")
    same([c.name for c in candidates], ["wezterm-1.Ubuntu22.04.deb"])


@check("a tag feed that cannot be read leaves the release as it was")
def _():
    # NetworkError was never imported here, so this raised NameError.
    empty = github.Release(repo="a/b", tag="v1", version="1")
    with patched(project.github,
                 recent_tags=_raise(NetworkError("HTTP 404"))):
        found, candidates = project._looking_back("a/b", empty, Quiet())
    same((found, candidates), (empty, []))


@check("a package's name survives the hyphens in it")
def _():
    # Cutting at the first hyphen turned sublime-text into sublime.
    for name, want in (
            ("ungoogled-chromium-151.0.7922.173-1-x86_64_linux.tar.xz",
             "ungoogled-chromium"),
            ("Godot_v4.7.2-stable_linux.x86_64.zip", "Godot"),
            ("btop-x86_64-unknown-linux-musl.tar.gz", "btop"),
            ("zen.linux-x86_64.tar.xz", "zen"),
            ("nvim-linux-x86_64.tar.gz", "nvim"),
            ("Spotube-linux-x86_64.deb", "Spotube")):
        same(local.name_from(Path(name)), want, name)


@check("a .deb is asked what it is called rather than guessed at")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        # The file name says "sublime"; the control file is the authority.
        deb = here / "sublime-text_build-4200_amd64.deb"
        make_deb(deb, package="sublime-text", version="4200")
        same(local.name_from(deb), "sublime-text")
        same(local.version_of(deb), "4200")


@check("a glob matches the same file in every version of it")
def _():
    for name, version, want in (
            ("discord-1.0.155.deb", "1.0.155", "discord-*.deb"),
            ("freetube_0.25.2_amd64.deb", "0.25.2", "freetube_*_amd64.deb"),
            ("Godot_v4.7.2-stable_linux.zip", "4.7.2",
             "Godot_v*-stable_linux.zip"),
            # A name with no version is its own glob, overwritten in place.
            ("zen.linux-x86_64.tar.xz", "1.21.15b", "zen.linux-x86_64.tar.xz")):
        same(local.glob_for(name, version), want, name)


@check("a path is told apart from a repository by being there")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        (here / "demo_1_amd64.deb").write_bytes(b"")
        assert local.looks_like_path(str(here))
        assert local.looks_like_path(str(here / "demo_1_amd64.deb"))
    # owner/name has a slash in it and so does ./a/b; only one is a path.
    assert not local.looks_like_path("aristocratos/btop")
    assert not local.looks_like_path("https://github.com/a/b")
    assert not local.looks_like_path("")


@check("a file in the folder is what a local upstream reports")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        make_deb(here / "demo_1.0_amd64.deb", version="1.0")
        found = sources.resolve({"kind": "local", "glob": "demo_*_amd64.deb"},
                                directory=here)
        same(found.version, "1.0")
        same(found.asset, "demo_1.0_amd64.deb")
        # `path` is what says there is nothing to download.
        same(found.path, str(here / "demo_1.0_amd64.deb"))

        make_deb(here / "demo_2.0_amd64.deb", version="2.0")
        same(sources.resolve({"kind": "local", "glob": "demo_*_amd64.deb"},
                             directory=here).version, "2.0")


@check("a local upstream with nothing to point at says where it looked")
def _():
    with tempfile.TemporaryDirectory() as home:
        try:
            sources.resolve({"kind": "local", "glob": "*.deb"},
                            directory=Path(home))
            assert False, "should have raised"
        except sources.NetworkError as exc:
            assert home in str(exc), str(exc)
    # A record naming no directory must not resolve against the cwd.
    with raises(sources.NetworkError, "should have raised"):
        sources.resolve({"kind": "local", "glob": "*.deb"})


@check("a newer file in the folder reads as an update, and then packages it")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        make_deb(here / "demo_1.0_amd64.deb", version="1.0")
        snap = db.Snap(name="demo", style="artifact", version="1.0",
                       kind="deb", asset="demo_1.0_amd64.deb",
                       asset_glob="demo_*_amd64.deb", directory=str(here),
                       upstream={"kind": "local", "glob": "demo_*_amd64.deb"})
        snap.snapcraft_yaml = ("name: demo\nversion: '1.0'\nparts:\n"
                               "  demo:\n    source: demo_1.0_amd64.deb\n")
        (here / "snap").mkdir()
        (here / "snap/snapcraft.yaml").write_text(snap.snapcraft_yaml)

        release, asset, _note = update.check(snap)
        same(asset, None, "nothing new yet")

        make_deb(here / "demo_2.0_amd64.deb", version="2.0")
        release, asset, _note = update.check(snap)
        same(release.version, "2.0")
        same(asset.name, "demo_2.0_amd64.deb")

        update.update(snap, release, asset, Quiet())
        same(snap.version, "2.0")
        assert "demo_2.0_amd64.deb" in (here / "snap/snapcraft.yaml").read_text()
        # The superseded one goes: two matches is one too many to open.
        assert not (here / "demo_1.0_amd64.deb").exists()


@check("packaging a file needs no download and writes no checksum")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        make_deb(here / "demo_1.2.3_amd64.deb", version="1.2.3",
                 binary="usr/bin/demo")
        made = project.plan_local(here / "demo_1.2.3_amd64.deb", Quiet())
        same(type(made.origin).__name__, "File")
        same(made.name, "demo")
        same(made.origin.version, "1.2.3")
        # The heading used to read a repository and tag, which a file lacks.
        assert made.title.endswith("demo_1.2.3_amd64.deb"), made.title

        snap = project.create(made, Quiet(), directory=str(here / "project"))
        same(snap.version, "1.2.3")
        same(snap.repo, "", "a file says nothing about a repository")
        same(snap.style, "artifact")
        same(snap.upstream, {"kind": "local", "glob": "demo_*_amd64.deb"})
        # Named beside its recipe, so the project can move and still build.
        assert (here / "project/demo_1.2.3_amd64.deb").is_file()
        assert "source: demo_1.2.3_amd64.deb" in snap.snapcraft_yaml
        # No source-checksum: checking a file against itself states nothing.
        assert "source-checksum" not in snap.snapcraft_yaml


@check("a file this tool cannot package is refused, with the reason")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        (here / "demo-1.0.rpm").write_bytes(b"")
        try:
            project.plan_local(here / "demo-1.0.rpm", Quiet())
            assert False, "should have raised"
        except project.ForgeError as exc:
            assert "somewhere else" in str(exc), str(exc)
        try:
            project.plan_local(here, Quiet())
            assert False, "an empty folder should have raised"
        except project.ForgeError as exc:
            assert "no package in" in str(exc), str(exc)
