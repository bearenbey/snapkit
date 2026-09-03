#!/usr/bin/env python3
"""Tests."""

import io
import json
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PASSED, FAILED = [], []


def check(name):
    """Decorator: run a function, and remember whether it raised."""
    def wrap(function):
        try:
            function()
            PASSED.append(name)
        except AssertionError as exc:
            FAILED.append((name, str(exc) or "assertion failed"))
        except Exception as exc:                          # noqa: BLE001
            FAILED.append((name, f"{type(exc).__name__}: {exc}"))
        return function
    return wrap


def same(got, want, what=""):
    assert got == want, f"{what}: {got!r} != {want!r}"


def subprocess_result(returncode=0):
    """What a patched subprocess.run hands back."""
    return type("Result", (), {"returncode": returncode, "stdout": "",
                               "stderr": ""})()


# -- a .deb, made here so the reader can be tested without the network --------

def make_deb(path, package="demo", version="1.2.3", binary="usr/bin/demo"):
    """A minimal but real .deb: an ar archive of three members."""
    def tar_gz(add):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            add(tar)
        return buffer.getvalue()

    def control(tar):
        text = (f"Package: {package}\nVersion: {version}\n"
                f"Architecture: amd64\nHomepage: https://example.invalid\n"
                f"Description: a demonstration\n so much to demonstrate\n").encode()
        info = tarfile.TarInfo("./control")
        info.size = len(text)
        tar.addfile(info, io.BytesIO(text))

    def data(tar):
        elf = b"\x7fELF" + b"\0" * 60          # enough for the magic check
        info = tarfile.TarInfo("./" + binary)
        info.size, info.mode = len(elf), 0o755
        tar.addfile(info, io.BytesIO(elf))
        entry = (b"[Desktop Entry]\nType=Application\nName=Demo\n"
                 b"Exec=demo\nTerminal=false\nIcon=demo\n")
        info = tarfile.TarInfo("./usr/share/applications/demo.desktop")
        info.size, info.mode = len(entry), 0o644
        tar.addfile(info, io.BytesIO(entry))

    members = [("debian-binary", b"2.0\n"),
               ("control.tar.gz", tar_gz(control)),
               ("data.tar.gz", tar_gz(data))]
    with open(path, "wb") as out:
        out.write(b"!<arch>\n")
        for name, blob in members:
            out.write(f"{name:<16}{'0':<12}{'0':<6}{'0':<6}{'100644':<8}"
                      f"{len(blob):<10}".encode() + b"`\n")
            out.write(blob)
            if len(blob) % 2:
                out.write(b"\n")
    return path


# -- offline ------------------------------------------------------------------

def upstreams():
    """Reading an upstream: the repository name, and which asset to take."""
    from snapforge import classify, github, project, update

    @check("github.parse_repo takes a url in any of its shapes")
    def _():
        for text in ("imputnet/helium-linux",
                     "https://github.com/imputnet/helium-linux",
                     "https://github.com/imputnet/helium-linux/",
                     "git@github.com:imputnet/helium-linux.git",
                     "github.com/imputnet/helium-linux/releases/tag/0.1",
                     "https://github.com/imputnet/helium-linux.git?tab=readme"):
            same(github.parse_repo(text), "imputnet/helium-linux", text)
        for bad in ("", "not a url", "https://gitlab.com/a/b"):
            try:
                github.parse_repo(bad)
                assert False, f"{bad!r} should not parse"
            except ValueError:
                pass
    @check("release notes in the tag feed are not mistaken for tags")
    def _():
        # The escaped release notes in the feed carry tag links of their own.
        feed = (
            '<feed><entry>'
            '<link rel="alternate" type="text/html" '
            'href="https://github.com/a/b/releases/tag/v0.4.0"/>'
            '<content type="html">Download from the '
            '&lt;a href=&quot;https://github.com/a/b/releases/tag/v0.4.0&quot;'
            '&gt;Releases page&lt;/a&gt;.</content>'
            '</entry><entry>'
            '<link rel="alternate" type="text/html" '
            'href="https://github.com/a/b/releases/tag/v0.3.0"/>'
            '</entry></feed>')
        with patched(github, get_text=lambda url, timeout=30: feed):
            same(github.recent_tags("a/b"), ["v0.4.0", "v0.3.0"])

    @check("github.version_of undecorates a tag")
    def _():
        for tag, want in (("v1.4.7", "1.4.7"), ("4.7.2-stable", "4.7.2"),
                          ("1.13.1", "1.13.1"), ("v0.25.2-beta", "0.25.2-beta"),
                          ("release-2.1", "2.1"), ("app@1.2.3", "1.2.3")):
            same(github.version_of(tag), want, tag)
    @check("classify keeps x86_64 and drops everything else")
    def _():
        # Splitting on separators turned x86_64 into x86, so 64-bit read as 32.
        for name in ("btop-x86_64-unknown-linux-musl.tar.gz",
                     "nvim-linux-x86_64.tar.gz", "freetube_0.25_amd64.deb",
                     "helium-0.15-x86_64_linux.tar.xz", "app-linux-x64.zip"):
            same(classify.rejection(name), "", name)
        for name, why in (("btop-m68k-unknown-linux-musl.tar.gz", "m68k"),
                          ("btop-i686-unknown-linux-musl.tar.gz", "i686"),
                          ("nvim-linux-arm64.tar.gz", "arm64"),
                          ("app_armv7l.deb", "armv7l"),
                          ("nvim-macos-x86_64.tar.gz", "macos"),
                          ("app-1.0-win64.zip", "win64"),
                          ("app.tar.gz.sha256", "checksum"),
                          ("app.rpm", "somewhere else")):
            assert why in classify.rejection(name), f"{name}: {classify.rejection(name)}"
    @check("classify ranks a deb over an archive over an appimage")
    def _():
        kinds = [classify.score(n)[0] for n in
                 ("app_1.0_amd64.deb", "app-1.0-linux-x86_64.tar.gz",
                  "app-1.0-x86_64.AppImage")]
        assert kinds[0] > kinds[1] > kinds[2], kinds
    @check("classify.asset_pattern still matches the next release")
    def _():
        class Asset:
            def __init__(self, name):
                self.name = name
        pattern = classify.asset_pattern("freetube_0.25.2_beta_amd64.deb",
                                         "0.25.2-beta")
        assert classify.match_pattern([Asset("freetube_0.26.0_beta_amd64.deb")],
                                      pattern)
        assert not classify.match_pattern([Asset("freetube_0.26.0_arm64.deb")],
                                          pattern)
        # A name with no version in it is matched literally.
        same(classify.asset_pattern("nvim-linux-x86_64.tar.gz", "0.12.4"),
             r"^nvim\-linux\-x86_64\.tar\.gz$")
    @check("a windows build is not mistaken for a linux one")
    def _():
        # mpv's -w64-mingw32.zip named the right arch and read as a Linux build.
        for name in ("mpv-v0.41.0-x86_64-w64-mingw32.zip",
                     "app-x86_64-pc-windows-msvc.zip",
                     "app-x86_64-w32-mingw.zip", "app-x86_64-cygwin.tar.gz"):
            assert classify.rejection(name), f"{name} was kept"
        for name in ("app-x86_64-unknown-linux-gnu.tar.gz", "app_amd64.deb"):
            same(classify.rejection(name), "", name)
    @check("choose() takes an asset by name or by number")
    def _():
        class C:
            def __init__(self, name):
                self.name = name
        candidates = [C("a.tar.gz"), C("b.deb"), C("c.AppImage")]
        same(project.choose(candidates).name, "a.tar.gz", "default")
        same(project.choose(candidates, "b.deb").name, "b.deb", "by name")
        same(project.choose(candidates, "3").name, "c.AppImage", "by number")
        for bad in ("nope", "0", "4"):
            try:
                project.choose(candidates, bad)
                assert False, f"{bad} should not resolve"
            except project.ForgeError as exc:
                assert "a.tar.gz" in str(exc), "the error should list the options"
    @check("a companion package does not outrank the application")
    def _():
        # clamui-privileged-helper scores identically and sorted first.
        class Asset:
            def __init__(self, name):
                self.name, self.url = name, "http://x/" + name
        assets = [Asset("clamui-privileged-helper_0.4.0_all.deb"),
                  Asset("clamui_0.4.0_all.deb")]
        same(classify.classify(assets, wanted="clamui")[0].name,
             "clamui_0.4.0_all.deb")
        # With nothing to compare against, the shorter name still wins.
        same(classify.classify(assets)[0].name, "clamui_0.4.0_all.deb")

    @check("the name a file leads with is what the project is called")
    def _():
        for name, wanted in (("clamui_0.4.0_all.deb", "clamui"),
                             ("clamui-privileged-helper_0.4.0_all.deb",
                              "clamui-privileged-helper"),
                             ("shotcut-linux-x86_64-26.8.1.txz",
                              "shotcut-linux-x86"),
                             ("nvim-linux-x86_64.tar.gz", "nvim-linux-x86"),
                             ("lutris_0.5.22_all.deb", "lutris")):
            same(classify.leading_name(name), wanted, name)

    @check("a release with nothing usable says what it does have")
    def _():
        class Asset:
            def __init__(self, name):
                self.name = name

        class Empty:
            tag, version, assets = "v1", "1", []

        class OnlyForeign:
            tag, version, assets = "v1", "1", [Asset("app-arm64.deb"),
                                               Asset("app.exe")]
        message = project._nothing_usable("a/b", Empty())
        assert "no files attached" in message, message
        assert not message.rstrip().endswith(":"), "the sentence trails off"
        message = project._nothing_usable("a/b", OnlyForeign())
        assert "app-arm64.deb -- built for arm64" in message, message
        assert "app.exe" in message, message


def architectures():
    """Which architecture this is, and which of a release's files is for it."""
    import os
    from snapforge import arch, classify, recipe, sources
    from snapforge.net import NetworkError

    class as_arch:
        """Run a block as though this machine were another architecture."""

        def __init__(self, name):
            self.name = name

        def __enter__(self):
            self.was = os.environ.get(arch.OVERRIDE)
            os.environ[arch.OVERRIDE] = self.name
            return self

        def __exit__(self, *_):
            if self.was is None:
                os.environ.pop(arch.OVERRIDE, None)
            else:
                os.environ[arch.OVERRIDE] = self.was
            return False

    @check("this machine's architecture is the one snapd would name")
    def _():
        # dpkg is the authority, because snapd agrees with it; uname otherwise.
        same(arch.known(arch.detected()), True,
             f"{arch.detected()} is not in the spellings table")
        for machine, wanted in (("x86_64", "amd64"), ("aarch64", "arm64"),
                                ("armv7l", "armhf"), ("ppc64le", "ppc64el"),
                                ("riscv64", "riscv64"), ("i686", "i386")):
            same(arch.FROM_MACHINE[machine], wanted, machine)

    @check("the override takes the spelling a person would actually type")
    def _():
        # x86_64 is what gets typed, and made every amd64 asset foreign.
        for typed, wanted in (("x86_64", "amd64"), ("aarch64", "arm64"),
                              ("AMD64", "amd64"), (" amd64 ", "amd64"),
                              ("ppc64le", "ppc64el"), ("arm64", "arm64")):
            with as_arch(typed):
                same(arch.host(), wanted, typed)
                if wanted == "amd64":
                    same(classify.rejection("app_amd64.deb"), "", typed)

    @check("an override nothing recognises is refused, not taken literally")
    def _():
        for typed in ("nonsense", "sparc", "x86_65"):
            with as_arch(typed):
                try:
                    arch.host()
                    assert False, f"{typed} should have been refused"
                except arch.UnknownArchitecture as exc:
                    assert "is not an architecture this knows" in str(exc)
                    # The message has to list them, or there is no way back.
                    assert "amd64" in str(exc) and "riscv64" in str(exc)
        # A ValueError, which the command line already turns into a message.
        assert issubclass(arch.UnknownArchitecture, ValueError)

    @check("a machine this does not know about is still allowed to be itself")
    def _():
        # Only what a person typed: a real port not in the table still runs.
        import shutil
        was_which, was_machine = arch.shutil.which, arch.platform.machine
        arch.detected.cache_clear()
        arch.shutil.which = lambda name: None if name == "dpkg" else was_which(name)
        arch.platform.machine = lambda: "sparc64"
        try:
            same(arch.detected(), "sparc64")
            same(arch.known("sparc64"), False, "it should not be in the table")
        finally:
            arch.shutil.which, arch.platform.machine = was_which, was_machine
            arch.detected.cache_clear()

    @check("an asset is ours or somebody else's depending on the host")
    def _():
        # Hardcoded x86_64 made every asset foreign on every other machine.
        cases = (("btop-x86_64-unknown-linux-musl.tar.gz", "amd64"),
                 ("nvim-linux-arm64.tar.gz", "arm64"),
                 ("app_armv7l.deb", "armhf"),
                 ("app-riscv64.deb", "riscv64"),
                 ("app-ppc64le.deb", "ppc64el"),
                 ("app-linux-x64.zip", "amd64"),
                 ("app-i686.deb", "i386"))
        for name, belongs_to in cases:
            with as_arch(belongs_to):
                same(classify.rejection(name), "", f"{name} on {belongs_to}")
            for stranger in ("amd64", "arm64", "riscv64"):
                if stranger == belongs_to:
                    continue
                with as_arch(stranger):
                    assert classify.rejection(name), \
                        f"{name} was kept on {stranger}"

    @check("x86 is 32-bit, and x86_64 is not x86 with something after it")
    def _():
        # The bug this guards: splitting on separators read x86_64 as 32-bit.
        with as_arch("amd64"):
            same(classify.rejection("app-x86_64.deb"), "")
            assert "x86" in classify.rejection("app-x86.deb")
        with as_arch("i386"):
            same(classify.rejection("app-x86.deb"), "")
            assert "x86_64" in classify.rejection("app-x86_64.deb")

    @check("a release with nothing for this machine says so in its own words")
    def _():
        with as_arch("arm64"):
            _points, _kind, why = classify.score("nvim-linux-arm64.tar.gz")
            assert "arm64 (arm64)" in why, why
        with as_arch("amd64"):
            _points, _kind, why = classify.score("btop-x86_64-linux.tar.gz")
            assert "amd64 (x86_64)" in why, why

    @check("an asset naming no architecture is taken whatever the host is")
    def _():
        # Usually the only build there is, and rejecting it packages nothing.
        for name in ("yt-dlp_linux.zip", "app.tar.gz"):
            for where in ("amd64", "arm64", "s390x"):
                with as_arch(where):
                    same(classify.rejection(name), "", f"{name} on {where}")

    @check("an upstream keeps {arch} so the record works on another machine")
    def _():
        # snap-db carries records between machines, so no baked-in binary-amd64.
        made = sources.configure("apt", {"base": "https://x/apt",
                                         "package": "thing"})
        assert "{arch}" in made["index"], made["index"]
        for where in ("amd64", "arm64", "riscv64"):
            with as_arch(where):
                same(sources._fill(made["index"], base="https://x/apt"),
                     f"https://x/apt/dists/stable/main/binary-{where}/Packages")

    @check("{arch} is a placeholder every shape can fill in")
    def _():
        for kind, values in (
                ("index", {"url": "https://x/{arch}/", "pattern": "(a)",
                           "asset": "a-{version}-{arch}.tar.xz"}),
                ("redirect", {"url": "https://x/{arch}", "pattern": "(a)",
                              "asset": "a-{arch}.deb",
                              "download": "https://x/{version}/{asset}"}),
                ("tag-archive", {"repo": "a/b", "asset": "a-{arch}.tar.gz",
                                 "download": "https://x/{tag}/{arch}"})):
            made = sources.configure(kind, values)
            same(made["asset"], values["asset"], kind)
        # And still refused where the shape genuinely cannot fill one in.
        try:
            sources.configure("index", {"url": "u", "pattern": "(a)",
                                        "asset": "a-{tag}"})
            assert False, "{tag} should still be refused for index"
        except sources.BadUpstream:
            pass

    @check("a written recipe names the architecture it was written on")
    def _():
        with as_arch("arm64"):
            text = recipe.build(name="demo", version="1.0", summary="a demo",
                                description="body", license_id="MIT",
                                kind="archive", url="https://x/a.tar.gz",
                                command="bin/demo")
            assert "\nplatforms:\n  arm64:\n" in text, text
        with as_arch("ppc64el"):
            text = recipe.build(name="demo", version="1.0", summary="a demo",
                                description="body", license_id="MIT",
                                kind="archive", url="https://x/a.tar.gz",
                                command="bin/demo")
            assert "\nplatforms:\n  ppc64el:\n" in text, text

    @check("an apt index is read for this machine, and for `all`")
    def _():
        from snapforge import versions
        index = ("Package: thing\nVersion: 2.0\nArchitecture: amd64\n"
                 "Filename: pool/thing_2.0_amd64.deb\nSHA256: aa\n\n"
                 "Package: thing\nVersion: 3.0\nArchitecture: arm64\n"
                 "Filename: pool/thing_3.0_arm64.deb\nSHA256: bb\n\n"
                 "Package: docs\nVersion: 9.0\nArchitecture: all\n"
                 "Filename: pool/docs_9.0_all.deb\nSHA256: cc\n")
        with patched(versions, get_text=lambda url, timeout=30: index):
            same(versions.apt_stanza("u", "thing", want_arch="amd64")[0], "2.0")
            same(versions.apt_stanza("u", "thing", want_arch="arm64")[0], "3.0")
            # Architecture: all is installable anywhere, so it is not skipped.
            same(versions.apt_stanza("u", "docs", want_arch="s390x")[0], "9.0")
            try:
                versions.apt_stanza("u", "thing", want_arch="s390x")
                assert False, "it should have said there is nothing"
            except NetworkError as exc:
                assert "s390x" in str(exc), str(exc)


def recipes():
    """Writing a snapcraft.yaml, and moving one onto a newer release."""
    from snapforge import classify, db, recipe

    @check("recipe.snap_name produces a name snapd will take")
    def _():
        for text, want in (("helium-linux", "helium"), ("FreeTube", "freetube"),
                           ("my_cool_app", "my-cool-app"), ("signal-desktop", "signal"),
                           ("draw.io-desktop", "draw-io"), ("2fa", "s-2fa")):
            same(recipe.snap_name(text), want, text)
    @check("recipe.build emits what snapcraft needs")
    def _():
        text = recipe.build(name="demo", version="1.0", summary="a demo",
                            description="body", license_id="MIT", kind="deb",
                            url="https://x/a.deb", command="usr/bin/demo",
                            desktop="usr/share/applications/demo.desktop",
                            icon="snap/gui/demo.png", traits={"gui", "electron"},
                            sha="abc", repo_url="https://r", title="demo")
        for needed in ("name: demo", "version: '1.0'", "license: MIT",
                       "platforms:", "extensions: [gnome]", "source-type: deb",
                       "source-checksum: sha256/abc", "icon: snap/gui/demo.png",
                       "- browser-support"):
            assert needed in text, needed
        # A terminal program gets neither the extension nor the desktop plugs.
        plain = recipe.build(name="d", version="1", summary="s", description="b",
                             license_id="", kind="archive", url="u",
                             command="bin/d", traits={"terminal"})
        assert "extensions" not in plain and "audio-playback" not in plain
    @check("a desktop entry's app id becomes the bus name the snap may own")
    def _():
        # A GtkApplication owns its id on the bus, and snapd refuses undeclared.
        for desktop, wanted in (
                ("usr/share/applications/net.lutris.Lutris.desktop",
                 "net.lutris.Lutris"),
                ("usr/share/applications/io.github.linx_systems.ClamUI.desktop",
                 "io.github.linx_systems.ClamUI"),
                ("share/applications/org.shotcut.Shotcut.desktop",
                 "org.shotcut.Shotcut"),
                # Not reverse-DNS, so not a bus name.
                ("usr/share/applications/freetube.desktop", ""),
                ("usr/share/applications/demo.desktop", ""),
                ("", "")):
            same(recipe.app_id(desktop), wanted, desktop or "(none)")

    @check("a recipe declares the bus name, and only when there is one")
    def _():
        text = recipe.build(
            name="clamui", version="0.4.0", summary="a demo",
            description="body", license_id="MIT", kind="deb",
            url="https://x/a.deb", command="usr/bin/clamui",
            desktop="usr/share/applications/io.github.linx_systems.ClamUI.desktop")
        assert "\nslots:\n  dbus-clamui:" in text, text
        assert "    name: io.github.linx_systems.ClamUI" in text, text
        assert "      - dbus-clamui" in text, text
        # A bare desktop name is not a bus name, so nothing is declared.
        plain = recipe.build(
            name="freetube", version="1.0", summary="a demo",
            description="body", license_id="MIT", kind="deb",
            url="https://x/a.deb", command="usr/bin/freetube",
            desktop="usr/share/applications/freetube.desktop")
        assert "slots:" not in plain, plain

    @check("the AppImage recipe finds the file whatever its extension looks like")
    def _():
        # neovim ships .appimage; a glob for *.AppImage alone failed at chmod.
        import subprocess
        text = recipe.build(name="d", version="1", summary="s", description="b",
                            license_id="", kind=classify.APPIMAGE,
                            url="https://x/d.appimage", command="usr/bin/d")
        line = next(l.strip() for l in text.splitlines()
                    if l.strip().startswith("image=$("))
        for spelling in ("d.AppImage", "d.appimage", "d.APPIMAGE", "d.AppIMAGE"):
            same(classify.kind_of(spelling), classify.APPIMAGE, spelling)
            with tempfile.TemporaryDirectory() as work:
                Path(work, spelling).touch()
                got = subprocess.run(["sh", "-c", line + "; echo $image"],
                                     cwd=work, capture_output=True, text=True)
                same(got.stdout.strip(), spelling, f"the glob missed {spelling}")
    @check("recipe.repoint moves only version, url and checksum")
    def _():
        before = recipe.build(name="d", version="1.0", summary="s",
                              description="b", license_id="", kind="archive",
                              url="https://x/d-1.0.tar.gz", command="bin/d",
                              sha="old")
        edited = before.replace("      - home", "      - home\n      - joystick")
        after = recipe.repoint(edited, "1.0", "2.0",
                               "https://x/d-1.0.tar.gz", "https://x/d-2.0.tar.gz",
                               sha="new")
        assert "version: '2.0'" in after and "d-2.0.tar.gz" in after
        assert "sha256/new" in after
        assert "- joystick" in after, "a hand edit was lost"
    @check("what the register says about a snap is what the recipe gets")
    def _():
        # `snapkit show` prints these, so editing one must do something.
        class Payload:
            summary = description = desktop = ""
            traits = set()
        snap = db.Snap(name="d", repo="a/b", kind="archive", version="1.0",
                       command="bin/d", license="MIT", confinement="classic",
                       grade="devel", base="core22", plugs=["home", "joystick"])
        text = recipe.from_record(snap, Payload(), "https://x/d.tar.gz")
        for needed in ("confinement: classic", "grade: devel", "base: core22",
                       "license: MIT", "- joystick"):
            assert needed in text, f"{needed} did not reach the recipe"


def register():
    """The register: one file per snap, the recipe beside it, and migration."""
    from snapforge import db, github, recipe

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
        import time as _time
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            store = db.Database(root)
            recipe = "name: x\n" + ("# padding to a realistic size\n" * 100)
            for index in range(1000):
                store.add(db.Snap(name=f"pkg{index:04d}", repo=f"o/p{index}",
                                  version="1.0", summary="a package",
                                  recipe_text=recipe))
            same(len(store), 1000)

            start = _time.perf_counter()
            reopened = db.Database(root)
            load = _time.perf_counter() - start
            same(len(reopened), 1000, "not all of them came back")

            # Best of a few: one sample of a millisecond of disk is noise.
            writes = []
            for _ in range(5):
                start = _time.perf_counter()
                reopened.add(reopened.get("pkg0500"))
                writes.append(_time.perf_counter() - start)
            write = min(writes)

            # The shape, not the numbers: one snap is not the whole register.
            assert write < load / 4, (
                f"one write took {write*1000:.1f} ms against a {load*1000:.1f} ms "
                f"read -- writes are not staying local")
            assert load < 2.0, f"reading 1000 records took {load:.2f} s"
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
            names = lambda text: [s.name for s in store.search(text)]
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
            try:
                db.Database(path)
                assert False, "should have raised"
            except db.DatabaseError:
                pass


def payloads():
    """Opening what was downloaded: the .deb reader, and what is inside."""
    from snapforge import inspect as ins

    @check("the .deb reader finds the program, the entry and the version")
    def _():
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            deb = make_deb(work / "demo_1.2.3_amd64.deb")
            same(ins.control_fields(deb).get("Version"), "1.2.3")
            payload = ins.look(deb, "deb", work / "out", wanted="demo")
            same(payload.command, "usr/bin/demo")
            same(payload.version, "1.2.3")
            same(payload.desktop, "usr/share/applications/demo.desktop")
            assert "gui" in payload.traits, payload.traits
            assert payload.summary.startswith("a demonstration"), payload.summary
    @check("Terminal=true is a command-line program, not a window")
    def _():
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            root = work / "payload"
            (root / "usr/share/applications").mkdir(parents=True)
            entry = root / "usr/share/applications/demo.desktop"
            entry.write_text("[Desktop Entry]\nType=Application\nTerminal=true\n")
            relative = "usr/share/applications/demo.desktop"
            assert ins.is_terminal_app(root, relative)
            traits = ins.traits_of(root, relative)
            assert "terminal" in traits and "gui" not in traits, traits
    @check("a not-a-deb is refused rather than misread")
    def _():
        with tempfile.TemporaryDirectory() as work:
            fake = Path(work) / "x.deb"
            fake.write_bytes(b"this is not an ar archive at all")
            try:
                ins.look(fake, "deb", Path(work) / "out")
                assert False, "should have raised"
            except ins.InspectionError:
                pass


def reading_payloads():
    """What a payload is opened for: the program to run, the icon to show,"""
    from snapforge import inspect

    def tree(root, files):
        """A payload on disk: paths mapped to bytes, or "" for an empty file."""
        for name, blob in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob if isinstance(blob, bytes) else blob.encode())
            if not name.endswith((".desktop", ".svg", ".png")):
                path.chmod(0o755)
        return root

    @check("a program is an ELF binary or a script that says what runs it")
    def _():
        # Taking only ELF made every interpreted application unpackageable.
        with tempfile.TemporaryDirectory() as home:
            root = tree(Path(home), {
                "usr/games/lutris": "#! /usr/bin/python3\nprint(1)\n",
                "usr/bin/native": b"\x7fELF" + b"\0" * 60,
                "usr/share/doc/readme": "not executable at all\n",
            })
            (root / "usr/share/doc/readme").chmod(0o644)
            found = inspect.find_binaries(root)
            same(sorted(found), ["usr/bin/native", "usr/games/lutris"])
            # And the one named after the application comes first.
            same(inspect.rank_binaries(found, "lutris")[0], "usr/games/lutris")

    @check("a script that runs the binary is the thing to run")
    def _():
        # shotcut's launcher sets the Qt paths its binary needs.
        with tempfile.TemporaryDirectory() as home:
            root = tree(Path(home), {
                "bin/thing": b"\x7fELF" + b"\0" * 60,
                "thing": "#!/bin/sh\nexport QT_PLUGIN_PATH=lib/qt6\n"
                         "bin/thing \"$@\"\n",
            })
            found = inspect.rank_binaries(inspect.find_binaries(root), "thing")
            same(found[0], "bin/thing", "the binary still ranks first")
            same(inspect.launcher_among(root, found), "thing")

    @check("a binary with no wrapper around it is still the thing to run")
    def _():
        with tempfile.TemporaryDirectory() as home:
            root = tree(Path(home), {"bin/thing": b"\x7fELF" + b"\0" * 60})
            found = inspect.rank_binaries(inspect.find_binaries(root), "thing")
            same(inspect.launcher_among(root, found), "",
                 "it invented a launcher that is not there")
            # And a script that names nothing else is not a launcher either.
            root2 = tree(Path(home) / "two", {
                "bin/other": b"\x7fELF" + b"\0" * 60,
                "solo": "#!/bin/sh\necho hello\n"})
            found2 = inspect.rank_binaries(inspect.find_binaries(root2), "solo")
            same(inspect.launcher_among(root2, found2), "")

    @check("the icon is the one the desktop entry asks for")
    def _():
        # In hicolor a mimetype icon matches the name as well as the real one.
        with tempfile.TemporaryDirectory() as home:
            root = tree(Path(home), {
                "usr/share/applications/net.lutris.Lutris.desktop":
                    "[Desktop Entry]\nName=Lutris\nIcon=net.lutris.Lutris\n",
                "usr/share/icons/hicolor/scalable/mimetypes/"
                "application-x-lutris.svg": "<svg/>",
                "usr/share/icons/hicolor/scalable/apps/net.lutris.Lutris.svg":
                    "<svg/>",
            })
            desktop = inspect.find_desktop(root, "lutris")
            named = inspect.desktop_icon(root, desktop)
            # Path.stem would cut net.lutris.Lutris down to net.lutris.
            same(named, "net.lutris.lutris")
            same(inspect.find_icon(root, "lutris", named=named),
                 "usr/share/icons/hicolor/scalable/apps/net.lutris.Lutris.svg")
            # Even with no Icon= to go on, a mimetype icon is not the app's.
            same(inspect.find_icon(root, "lutris"),
                 "usr/share/icons/hicolor/scalable/apps/net.lutris.Lutris.svg")

    @check("an Icon= that is a path, or carries a suffix, still resolves")
    def _():
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            entry = root / "a.desktop"
            for wrote, wanted in (("Icon=/usr/share/pixmaps/thing.png", "thing"),
                                  ("Icon=thing.svg", "thing"),
                                  ("Icon=thing", "thing"),
                                  ("Icon = Thing ", "thing"),
                                  ("Name=no icon here", "")):
                entry.write_text(f"[Desktop Entry]\n{wrote}\n")
                same(inspect.desktop_icon(root, "a.desktop"), wanted, wrote)

    @check("a library the payload ships is not reported as missing")
    def _():
        # A bundled Qt6 read as nineteen missing libraries, so look beside it.
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            (root / "lib").mkdir()
            (root / "lib" / "libthing.so.6").write_bytes(b"\x7fELF")
            same([d.name for d in inspect.bundled_lib_dirs(root)], ["lib"])
            # No .so files means nothing to add to the search path.
            bare = Path(home) / "bare"
            (bare / "lib").mkdir(parents=True)
            same(inspect.bundled_lib_dirs(bare), [])


def projects():
    """Projects that exist already: importing one, and writing one back out."""
    from snapforge import db, github, project, recipe, update

    @check("a project deleted from disk comes back from the register, icon and all")
    def _():
        # The icon is kept beside the recipe, or the restored one names nothing.
        with tempfile.TemporaryDirectory() as home:
            import os
            was = os.environ.get("SNAPKIT_HOME")
            os.environ["SNAPKIT_HOME"] = home
            try:
                store = db.Database()
                icon_source = Path(home) / "source.png"
                icon_source.write_bytes(b"\x89PNG\r\n\x1a\n fake")
                snap = db.Snap(name="demo", repo="a/b", version="1.0",
                               icon="snap/gui/demo.png",
                               recipe_text="name: demo\nicon: snap/gui/demo.png\n")
                store.add(snap)
                snap.keep_icon(icon_source)
                reporter = __import__("snapforge.report", fromlist=["x"]).Reporter()
                project.write(snap, reporter)
                assert (snap.path / "snap/gui/demo.png").is_file()

                import shutil as sh
                sh.rmtree(snap.path)
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
            finally:
                if was is None:
                    os.environ.pop("SNAPKIT_HOME", None)
                else:
                    os.environ["SNAPKIT_HOME"] = was
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
            try:
                adopt.read(Path(work))
                assert False, "should have raised"
            except adopt.NotAProject:
                pass
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
            import shutil
            shutil.rmtree(directory)
            gone = db.Database(work / "register")
            same(gone.get("demo").version, "1.1.0",
                 "a missing project should not blank the recorded version")
            same(gone.resynced, [])
    @check("a record put right on load says so, rather than changing quietly")
    def _():
        import contextlib
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
            with patched(db, default_path=lambda: register):
                with contextlib.redirect_stdout(buffer):
                    cli.main(["list"])
            said = buffer.getvalue()
            assert "demo was recorded at 1.0.0" in said, said
            assert "2.0.0" in said, said

            # and nothing to say on the next command, because it is settled
            second = io.StringIO()
            with patched(db, default_path=lambda: register):
                with contextlib.redirect_stdout(second):
                    cli.main(["list"])
            assert "was recorded at" not in second.getvalue(), second.getvalue()

    @check("importing does not damage the project it imports")
    def _():
        # write() used to overwrite a README and add an empty recipe.
        from snapforge.report import Reporter
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
        from snapforge.report import Reporter
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
    @check("version_from reads a version out of a source url")
    def _():
        from snapforge.adopt import version_from
        for source, want in (
                ("https://github.com/mpv-player/mpv/archive/refs/tags/v0.41.0.tar.gz", "0.41.0"),
                ("https://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz", "9.0.1"),
                ("https://github.com/o/o/releases/download/v0.32.15/o-linux.tar.zst", "0.32.15"),
                ("https://github.com/irssi/irssi/releases/download/1.4.5/irssi-1.4.5.tar.xz", "1.4.5"),
                ("./sublime-text_build-4200_amd64.deb", "4200")):
            same(version_from(source, ""), want, source)


def checking():
    """Asking whether a snap is behind, and what to build the new one from."""
    from snapforge import db, project, update

    @check("a matching version is up to date even with no tag recorded")
    def _():
        # Comparing both made every imported project read as out of date.
        class Asset:
            def __init__(self, name):
                self.name, self.url = name, "http://x/" + name

        class Release:
            version, tag = "1.4.7", "v1.4.7"
            assets = [Asset("demo-1.4.7-x86_64-linux.tar.gz")]

        real = project.github.release
        project.github.release = lambda repo, tag=None: Release()
        try:
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
        finally:
            project.github.release = real
    @check("a snap with nothing to match against is not checked at all")
    def _():
        # A guessed upstream stays inert: Signal's .deb is not on GitHub at all.
        snap = db.Snap(name="signal-desktop", repo="signalapp/Signal-Desktop",
                       kind="deb", version="8.24.1", asset_pattern="")
        try:
            update.check(snap)
            assert False, "it went upstream anyway"
        except update.NotTracked:
            pass
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
        real = project.github.release
        project.github.release = lambda repo, tag=None: Release()
        try:
            release, asset, note = update.check(snap)
            same(asset.name, "demo-2.0-x86_64-linux.tar.gz")
            assert "no longer publishes" in note, note
        finally:
            project.github.release = real


def tracking():
    """Saying where a snap's releases come from, when it is not a release."""
    from snapforge import arch, cli, db, sources
    from snapforge.net import NetworkError

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
            try:
                update.retrack(snap, wanted)
                assert False, "it should have refused"
            except NetworkError:
                pass
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
        # settle() had its own copy of the rollback, and the dashboard had none.
        from snapforge import update
        class Args:
            force = False
        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home))
            snap = db.Snap(name="demo", version="1.0",
                           upstream={"kind": "local", "glob": "was_*.deb"})
            store.add(snap)
            wanted = sources.configure("apt", {"base": "https://x/apt",
                                               "package": "thing"})
            with patched(update, resolve=_raise(NetworkError("HTTP 404"))):
                try:
                    cli.settle(store, Args(), Quiet(), snap, wanted)
                    assert False, "it should have refused"
                except SystemExit:
                    pass
                same(db.Database(Path(home)).get("demo").upstream,
                     {"kind": "local", "glob": "was_*.deb"},
                     "the refusal did not reach the record")

                Args.force = True
                same(cli.settle(store, Args(), Quiet(), snap, wanted), 0)
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
            cli.untrack(store, snap, Quiet())
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
            for words, wanted in ((("nothing-like-this",), "nothing registered"),
                                  ((), "track needs a name")):
                try:
                    track(*words)
                    assert False, f"{words} should have exited"
                except SystemExit:
                    pass

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

    @check("track kinds prints every kind, so none is reachable but unlisted")
    def _():
        import contextlib
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            same(cli.cmd_track(None, cli.parse_args(["track", "kinds"]), Quiet()), 0)
        printed = buffer.getvalue()
        for kind in sources.SHAPES:
            assert f"  {kind}  --  " in printed, f"{kind} is not in the list"
        for shape in sources.SPECS:
            for key in shape.required:
                assert key in printed, f"{shape.kind}: {key} is not printed"

    @check("every shape says what it takes, so `track kinds` cannot go stale")
    def _():
        same(sorted(sources.SPEC), sorted(sources.SHAPES),
             "a shape without a spec is one `track` cannot reach")
        for shape in sources.SPECS:
            assert shape.summary and shape.example, shape.kind
            assert shape.example.startswith("snapkit track "), shape.kind
            for key in shape.required:
                assert key in shape.keys, f"{shape.kind}: {key} is undescribed"
            for key in shape.templates:
                assert key in shape.keys or key in sources.COMMON, \
                    f"{shape.kind}: {key} is templated but not a setting"


def _raise(exception):
    """A stand-in that raises what it was given, however it is called."""
    def raiser(*_args, **_kwargs):
        raise exception
    return raiser


def update_state(snap):
    from snapforge import update
    return update.situation(snap).state


def dashboard():
    """The dashboard: the keys, the one worker thread, and the drawing."""
    from snapforge import db

    @check("every header the find-or-add box can draw actually draws")
    def _():
        # screen.py used local.looks_like_path() and never imported it.
        from snapforge.tui import Dashboard

        with tempfile.TemporaryDirectory() as home:
            board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
            board.prompting = True
            for typed in ("", "owner/name", "./thing.deb", str(Path(home)),
                          "~/Downloads"):
                board.prompt, board.matches = typed, []
                board.screen.render()

    @check("the picker blocks the worker until something is chosen")
    def _():
        from snapforge.tui import Cancelled, Dashboard

        class Candidate:
            def __init__(self, name):
                self.name, self.kind, self.why = name, "archive", "because"

        class Release:
            tag = "v1"

        class Plan:
            repo, release = "a/b", Release()
            candidates = [Candidate("one.tar.gz"), Candidate("two.deb"),
                          Candidate("three.AppImage")]

        with tempfile.TemporaryDirectory() as home:
            board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
            for keys, want in ((["down", "enter"], "two.deb"),
                               (["3"], "three.AppImage"),
                               (["enter"], "one.tar.gz")):
                out = {}
                board.run_job("creating", lambda: out.update(
                    got=board._ask_which(Plan()).name))
                for _ in range(50):
                    if board.picking is not None:
                        break
                    time.sleep(0.02)
                assert board.picking is not None, "the picker never appeared"
                assert not out, "it chose without being asked"
                for key in keys:
                    board.handle(key)
                for _ in range(100):
                    if out:
                        break
                    time.sleep(0.02)
                same(out.get("got"), want, f"keys {keys}")
                assert board.picking is None, "the picker stayed up"

            # escape gives up on the whole create
            out = {}
            board.run_job("creating", lambda: out.update(
                raised=_catch(board, Plan, Cancelled)))
            for _ in range(50):
                if board.picking is not None:
                    break
                time.sleep(0.02)
            board.handle("escape")
            for _ in range(100):
                if out:
                    break
                time.sleep(0.02)
            same(out.get("raised"), True, "escape did not cancel")
    @check("the find-or-add box searches as you type and picks what it finds")
    def _():
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home) / "snapkit.json")
            store.add(db.Snap(name="btop", repo="aristocratos/btop",
                              summary="A monitor of resources"))
            store.add(db.Snap(name="bat", repo="sharkdp/bat", summary="a cat clone"))
            board = Dashboard(db=store)
            board.handle("n")
            for character in "monitor":
                board.handle(character)
            same([s.name for s in board.matches], ["btop"], "typing did not search")
            board.handle("backspace")
            same(board.prompt, "monito")
            for character in list("monito"):
                board.handle("backspace")
            same(board.prompt, "", "backspace did not empty it")

            # a repository nothing matches falls through to making a new one
            for character in "sharkdp/hyperfine":
                board.handle(character)
            same(board.matches, [], "an unknown repo should match nothing")
            board.handle("escape")
            same(board.prompting, False)
            same(board.matches, [], "escape left the matches behind")
    @check("the header is tall enough to show what it is showing")
    def _():
        # Fixed at three rows, the matches were drawn into nothing.
        import io as _io
        from rich.console import Console
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home) / "snapkit.json")
            for index in range(4):
                store.add(db.Snap(name=f"thing{index}", repo=f"a/thing{index}"))
            board = Dashboard(db=store)
            board.handle("n")
            for character in "thing":
                board.handle(character)
            assert len(board.matches) == 4, board.matches
            assert board.screen._header_height() >= 4 + 4, board.screen._header_height()
            buffer = _io.StringIO()
            Console(file=buffer, width=100, height=30).print(board.render())
            drawn = buffer.getvalue()
            for index in range(4):
                assert f"thing{index}" in drawn.split("registered")[0], \
                    f"thing{index} was clipped out of the header"
    @check("the dashboard actually starts its work")
    def _():
        # Every action set `busy` then hit a guard refusing anything busy.
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
            ran = threading.Event()
            assert board.run_job("testing", ran.set) is True, "job refused"
            assert ran.wait(5), "the job never ran"
            for _ in range(50):
                if not board.busy:
                    break
                time.sleep(0.05)
            same(board.busy, "", "busy was not cleared")
    @check("the dashboard refuses a second job while one is running")
    def _():
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
            release = threading.Event()
            board.run_job("first", release.wait)
            assert board.run_job("second", lambda: None) is False, "overlap allowed"
            release.set()
    @check("a job that raises says so and does not wedge the dashboard")
    def _():
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))

            def boom():
                raise RuntimeError("deliberate")
            board.run_job("boom", boom)
            for _ in range(50):
                if not board.busy:
                    break
                time.sleep(0.05)
            same(board.busy, "", "busy stuck after a failure")
            assert "deliberate" in str(list(board.log)[-1]), list(board.log)
    @check("an arrow key is an arrow key, not an Escape")
    def _():
        # stdin's buffer hid the rest of an arrow key, so it read as Escape.
        from snapforge.keys import Keyboard
        reader = Keyboard.__new__(Keyboard)
        reader.pending = ""
        for data, want in (("\x1b[A", ["up"]), ("\x1b[B", ["down"]),
                           ("\x1bOA", ["up"]), ("\x1bOB", ["down"]),
                           ("\x1b[C", ["right"]), ("\x1b[D", ["left"]),
                           ("\x1b[5~", ["pageup"]), ("\x1b[6~", ["pagedown"]),
                           ("\x1b[H", ["home"]), ("\x1b[F", ["end"]),
                           ("\r", ["enter"]), ("\x7f", ["backspace"]),
                           ("\x1b[A\x1b[A\x1b[B", ["up", "up", "down"]),
                           ("abc", ["a", "b", "c"])):
            reader.pending = ""
            same(reader._parse(data), want, repr(data))

        # a sequence split across two reads is still one key
        reader.pending = ""
        same(reader._parse("\x1b"), [], "half an arrow produced a key")
        same(reader._parse("[A"), ["up"], "the other half was lost")

        # and a real Escape is held, to be told apart from an arrow
        reader.pending = ""
        reader._parse("\x1b")
        same(reader.pending, "\x1b", "Escape was not held back")
    @check("only q quits")
    def _():
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home) / "snapkit.json")
            store.add(db.Snap(name="demo", repo="a/b"))
            board = Dashboard(db=store)
            for key in ("up", "down", "left", "right", "escape", "home", "end",
                        "pageup", "pagedown", "x", "tab"):
                board.handle(key)
                same(board.quit, False, f"{key} quit the dashboard")
            board.handle("q")
            same(board.quit, True, "q did not quit")
    @check("every key the legend advertises reaches what it advertises")
    def _():
        from snapforge import screen
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home) / "snapkit.json")
            for index in range(5):
                store.add(db.Snap(name=f"s{index}", repo=f"a/b{index}"))
            board = Dashboard(db=store)

            called = []
            for name in ("recheck", "update_selected", "build_selected",
                         "pull_database", "update_all"):
                setattr(board, name, lambda name=name: called.append(name))

            # g read the database, and an earlier binding took it for `home`.
            board.cursor = 3
            board.handle("g")
            same(called, ["pull_database"], "g did not read the database")
            same(board.cursor, 3, "g moved the cursor instead")

            for key, name in (("r", "recheck"), ("u", "update_selected"),
                              ("b", "build_selected"), ("U", "update_all")):
                called.clear()
                board.handle(key)
                same(called, [name], f"{key} did not reach {name}")

            # and the ones that only change what is on screen
            board.handle("l")
            same(board.reading_log, True, "l did not open the log")
            board.handle("q")
            same((board.reading_log, board.quit), (False, False),
                 "q in the log should close it, not quit the dashboard")
            board.handle("?")
            same(board.helping, True, "? did not open the keys")
            board.handle("x")
            same(board.helping, False, "the keys page would not close")
            board.handle("/")
            same(board.filtering, True, "/ did not open the filter")
            board.handle("escape")
            same((board.filtering, board.needle), (False, ""),
                 "escape left the filter behind")
            was = board.order
            board.handle("s")
            assert board.order != was, "s did not change the order"

            lettered = {key for key, _ in screen.KEYS + screen.SHORT_KEYS
                        if len(key) == 1}
            same(lettered - set("nrub/l?q"), set(),
                 "a key is advertised in the footer that nothing here presses")

    @check("the list scrolls, and the cursor stays inside it")
    def _():
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home) / "snapkit.json")
            for index in range(20):
                store.add(db.Snap(name=f"s{index:02d}", repo=f"a/b{index}"))
            board = Dashboard(db=store)
            board.screen.window = 8

            same(board.screen._window(), (0, 8), "the first window is wrong")
            for _ in range(10):
                board.handle("down")
            first, last = board.screen._window()
            assert first <= board.cursor < last, \
                f"cursor {board.cursor} outside the drawn window {first}-{last}"

            board.handle("end")
            same(board.cursor, 19, "end did not go to the last")
            first, last = board.screen._window()
            assert first <= 19 < last, f"the last row is not drawn: {first}-{last}"

            board.handle("home")
            board.screen._window()
            same((board.cursor, board.screen.offset), (0, 0), "home did not scroll back")

            # walking off either end changes nothing
            for _ in range(50):
                board.handle("up")
            same(board.cursor, 0)
            for _ in range(50):
                board.handle("down")
            same(board.cursor, 19)

            # a list shorter than the window is never scrolled
            board.rows = board.rows[:3]
            board.cursor = 0
            same(board.screen._window(), (0, 3))
            same(board.screen.offset, 0)
    @check("the dashboard checks the same things the command line does")
    def _():
        # Same finding, but through the recheck that held the bad pre-check.
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            here = Path(home)
            project_dir = here / "demo-snap"
            project_dir.mkdir()
            make_deb(project_dir / "demo_1.0_amd64.deb", version="1.0")
            store = db.Database(here / "register")
            store.add(db.Snap(
                name="demo", style="artifact", version="1.0", kind="deb",
                asset="demo_1.0_amd64.deb", asset_glob="demo_*_amd64.deb",
                directory=str(project_dir),
                upstream={"kind": "local", "glob": "demo_*_amd64.deb"}))

            board = Dashboard(db=store)
            make_deb(project_dir / "demo_2.0_amd64.deb", version="2.0")
            board.recheck()
            board.worker.join(timeout=20)
            same(board.rows[0].state, "behind", "the dashboard did not check it")
            same(board.rows[0].latest, "2.0")

    @check("t opens the track box seeded with what the snap tracks now")
    def _():
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home))
            store.add(db.Snap(name="demo", version="1.0",
                              upstream={"kind": "local", "glob": "demo_*.deb"}))
            board = Dashboard(db=store)
            board.handle("t")
            same(board.tracking, "demo")
            same(board.prompt, "local glob=demo_*.deb",
                 "editing one word should not mean typing all of them")
            # Typed into, then abandoned: nothing of it survives.
            for letter in " x":
                board.handle(letter)
            board.handle("escape")
            same((board.tracking, board.prompt), ("", ""))
            same(store.get("demo").upstream,
                 {"kind": "local", "glob": "demo_*.deb"})

    @check("an emptied track box is never mind, not stop tracking")
    def _():
        # Backing out of `t` with a cleared line threw the upstream away.
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home))
            was = {"kind": "local", "glob": "demo_*.deb"}
            store.add(db.Snap(name="demo", version="1.0", upstream=dict(was)))
            board = Dashboard(db=store)

            board.track("demo", "")
            if board.worker:
                board.worker.join(timeout=10)
            same(store.get("demo").upstream, was, "an empty line untracked it")
            same(board.busy, "", "it should not have started any work")

            # Said outright, it still does what it says.
            board.track("demo", "none")
            board.worker.join(timeout=10)
            same(store.get("demo").upstream, {})

    @check("stopping tracking clears the repository as well as the upstream")
    def _():
        # Left behind, `repo` has check() fall back to it and carry on.
        from snapforge import update
        snap = db.Snap(name="demo", repo="a/b", url="https://github.com/a/b",
                       version="1.0", asset_pattern="^x$",
                       upstream={"kind": "local"})
        update.untrack(snap)
        same((snap.upstream, snap.repo, snap.url, snap.asset_pattern),
             ({}, "", "", ""))
        same(update.situation(snap).state, "untracked")

    @check("the dashboard tracks, and refuses, the way the command line does")
    def _():
        from snapforge import update
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            here = Path(home)
            project_dir = here / "demo-snap"
            project_dir.mkdir()
            make_deb(project_dir / "demo_2.0_amd64.deb", version="2.0")
            store = db.Database(here / "register")
            store.add(db.Snap(name="demo", style="artifact", version="1.0",
                              kind="deb", asset_glob="demo_*_amd64.deb",
                              directory=str(project_dir)))
            board = Dashboard(db=store)

            board.track("demo", "folder glob=demo_*_amd64.deb")
            board.worker.join(timeout=20)
            same(store.get("demo").upstream,
                 {"kind": "local", "glob": "demo_*_amd64.deb"})
            same(board.rows[0].state, "behind", "it did not check what it set")
            same(board.rows[0].latest, "2.0")

            # Written down untried, a setting reads as up to date for ever.
            board.track("demo", "local glob=nothing-like-this-*.deb")
            board.worker.join(timeout=20)
            same(store.get("demo").upstream,
                 {"kind": "local", "glob": "demo_*_amd64.deb"},
                 "an upstream that resolved to nothing was written down")
            assert any("left as it was" in line.plain for line in board.log), \
                "it did not say why"

            board.track("demo", "none")
            board.worker.join(timeout=20)
            same(store.get("demo").upstream, {})
            same(board.rows[0].state, "untracked")

    @check("every kind reachable from the terminal is offered on the dashboard")
    def _():
        # The command line grew `track` first; the dashboard is the same list.
        from snapforge import screen, sources
        offered = {form.split()[0] for form, _ in screen._TRACK_HINTS}
        same(offered, set(sources.SHAPES) | {"repo", "none"},
             "the dashboard and the shapes have drifted")
        assert "t" in screen.advertised(), "no key says it is there"

    @check("every mode is answered by a key handler and drawn by something")
    def _():
        # Two chains that had to agree left a new mode stuck on screen.
        from snapforge import screen, tui

        same(set(tui.HANDLERS), set(tui.MODES),
             "a mode has no key handler, or a handler has no mode")
        same(set(screen.FULL_SCREEN) - set(tui.MODES), set(),
             "something is drawn full screen for a mode that cannot be up")

        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home) / "snapkit.json")
            store.add(db.Snap(name="demo", repo="a/b"))
            board = tui.Dashboard(db=store)
            same(board.mode, "", "the list is not a mode")
            for name in tui.MODES:
                setattr(board, name, "x" if name != "picking" else object())
                same(board.mode, name, f"{name} did not take the screen")
                setattr(board, name, "" if name != "picking" else None)
            same(board.mode, "", "a mode was left up")

    @check("a check that runs long is a timeout, not a wait")
    def _():
        import time as _time
        from snapforge import net, update

        # Nothing left on the clock: the request is refused before a socket.
        with net.deadline(0.05):
            _time.sleep(0.06)
            try:
                net.get_text("https://example.invalid/never-asked")
                assert False, "should have given up"
            except net.NetworkError as exc:
                assert "timed out" in str(exc), exc

        # With time left, a request is given what is left and no more.
        asked = []

        class Opener:
            @staticmethod
            def open(request, timeout=None):
                asked.append(timeout)
                raise OSError("not answering")

        with net.deadline(2):
            try:
                net._open(Opener, "https://example.invalid/", retries=0)
            except net.NetworkError:
                pass
        assert asked and asked[0] <= 2, f"asked for {asked}, not what was left"
        same(net._left(30, "u"), 30, "the deadline outlived its block")

        # And a snap whose upstream will not answer reads as unreachable.
        was = update.check

        def slow(snap, force=False):
            _time.sleep(0.2)
            raise net.NetworkError("https://nowhere.invalid/: timed out")

        update.check = slow
        try:
            found = update.situation(db.Snap(name="demo", repo="a/b"),
                                     timeout=0.05)
            same(found.state, "error", "a timeout is not an up-to-date")
            assert "timed out" in found.problem, found.problem
        finally:
            update.check = was

    @check("a filter narrows the eye, not the register")
    def _():
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home) / "snapkit.json")
            for name, repo in (("btop", "aristocratos/btop"),
                               ("zen", "zen-browser/desktop"),
                               ("floorp", "Floorp-Projects/Floorp")):
                store.add(db.Snap(name=name, repo=repo, version="1.0",
                                  summary="a browser" if name != "btop"
                                  else "a resource monitor"))
            board = Dashboard(db=store)
            same(len(board.rows), 3)

            board.handle("/")
            for key in "brow":
                board.handle(key)
            same([r.name for r in board.rows], ["floorp", "zen"],
                 "the filter reads the summary as well as the name")
            same(len(board.known), 3, "the filter threw records away")

            # r and U work on the register, so a filter cannot hide work.
            board.known[0].state = "behind"
            same([r.name for r in board.known if r.behind], ["btop"],
                 "a filtered-out row stopped being behind")

            board.handle("escape")
            same((board.needle, len(board.rows)), ("", 3), "escape left it narrowed")

    @check("ordering by attention puts what needs doing at the top")
    def _():
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home) / "snapkit.json")
            for name in ("aaa", "bbb", "ccc"):
                store.add(db.Snap(name=name, repo=f"o/{name}", version="1.0"))
            board = Dashboard(db=store)
            same([r.name for r in board.rows], ["aaa", "bbb", "ccc"])

            board.row_for("ccc").state = "behind"
            board.row_for("bbb").state = "failed"
            board.handle("s")
            same([r.name for r in board.rows], ["ccc", "bbb", "aaa"],
                 "behind, then failed, then the rest")
            board.handle("s")
            same([r.name for r in board.rows], ["aaa", "bbb", "ccc"],
                 "s did not put the register order back")

    @check("the log scrolls back, and stops at both ends")
    def _():
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home) / "snapkit.json")
            store.add(db.Snap(name="demo", repo="a/b"))
            board = Dashboard(db=store)
            for index in range(50):
                board.say(f"line {index}")

            # The drawing is what tells the scroll how long the page is.
            from rich.console import Console
            paper = Console(file=io.StringIO(), width=90, height=20)

            def press(key):
                board.handle(key)
                paper.print(board.render())

            press("l")
            same((board.reading_log, board.page_offset), (True, 0))
            for _ in range(5):
                press("up")
            same(board.page_offset, 5, "up did not scroll back")
            for _ in range(500):
                press("up")
            assert board.page_offset < len(board.log), "scrolled past the oldest"
            press("G")
            same(board.page_offset, 0, "G did not come back to the newest")
            press("escape")
            same(board.reading_log, False, "escape did not close the log")

    @check("delete asks before it forgets")
    def _():
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home) / "snapkit.json")
            store.add(db.Snap(name="demo", repo="a/b"))
            board = Dashboard(db=store)
            board.handle("d")
            same(board.confirm, "demo", "no confirmation was asked for")
            board.handle("n")
            same(store.names(), ["demo"], "answering no still deleted it")
            board.handle("d")
            board.handle("y")
            same(store.names(), [], "answering yes did not delete it")
    @check("the dashboard draws every state it can be in")
    def _():
        from rich.console import Console
        from snapforge.screen import STATE_STYLE
        from snapforge.tui import Dashboard
        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home) / "snapkit.json")
            for index, state in enumerate(STATE_STYLE):
                store.add(db.Snap(name=f"s{index}", repo=f"a/b{index}",
                                  version="1.0", kind="deb"))
            board = Dashboard(db=store)
            for row, state in zip(board.rows, STATE_STYLE):
                row.state = state
            board.rows[0].done_bytes, board.rows[0].total_bytes = 1, 2
            console = Console(file=io.StringIO(), width=120, height=40)
            console.print(board.render())
            board.prompting = True
            console.print(board.render())
            board.prompting, board.confirm = False, "s0"
            console.print(board.render())
            board.confirm, board.detail = "", board.rows[0].snap
            console.print(board.render())
            # The three that take the whole screen to themselves.
            board.detail, board.helping = None, True
            console.print(board.render())
            board.helping, board.reading_log = False, True
            console.print(board.render())
            board.reading_log, board.filtering, board.needle = False, True, "s1"
            console.print(board.render())
    @check("the screen fits the terminal it is given")
    def _():
        # A fixed column set squeezed REPOSITORY to nothing at eighty columns.
        from rich.console import Console
        from snapforge.screen import KEYS, _keys
        from snapforge.tui import Dashboard

        class FakeLive:          # render() reads the width off the console
            def __init__(self, console): self.console = console

        with tempfile.TemporaryDirectory() as home:
            store = db.Database(Path(home) / "snapkit.json")
            for index in range(4):
                store.add(db.Snap(name=f"thing{index}", repo=f"owner/thing{index}",
                                  version="1.0.0", kind="deb"))
            board = Dashboard(db=store)
            for width in (72, 80, 96, 110, 132, 200):
                console = Console(file=io.StringIO(), width=width, height=30)
                board.live = FakeLive(console)
                console.print(board.render())
                for line in console.file.getvalue().splitlines():
                    assert len(line.rstrip()) <= width, \
                        f"a line ran past {width} columns: {line!r}"
                console.file.truncate(0), console.file.seek(0)

                # every column that was drawn got room to say something
                console.print(board.screen._table(width))
                drawn = console.file.getvalue()
                assert "thing0" in drawn, f"the list vanished at {width} columns"

            # the inspector appears when there is room and not before
            wide = Console(file=io.StringIO(), width=132, height=30)
            board.live = FakeLive(wide)
            wide.print(board.render())
            assert "inspector" in wide.file.getvalue(), "no inspector at 132 columns"
            narrow = Console(file=io.StringIO(), width=90, height=30)
            board.live = FakeLive(narrow)
            narrow.print(board.render())
            assert "inspector" not in narrow.file.getvalue(), \
                "the inspector was drawn where there is no room for it"

            # and the legend shortens rather than being cut off
            same(len(board.screen._footer(200).plain) <= 200, True)
            assert len(board.screen._footer(60).plain) <= 60, "the legend overran"
            assert len(_keys(KEYS).plain) > 60, "this test proves nothing"
    @check("the moving parts move")
    def _():
        from snapforge.screen import _ago, _smooth_bar, _spinner
        # a bar that only moves a whole cell at a time looks stuck
        seen = {_smooth_bar(n / 100, 12) for n in range(101)}
        assert len(seen) > 12, f"only {len(seen)} distinct bars over 100 steps"
        same(_smooth_bar(0, 12), "░" * 12)
        same(_smooth_bar(1, 12), "█" * 12)
        for share in (-1, 0.5, 2):
            same(len(_smooth_bar(share, 12)), 12, f"wrong width at {share}")
        same(len({_spinner(f) for f in range(24)}) > 1, True, "the spinner is still")
        same(_ago(""), "")
        same(_ago("not a date"), "")
        assert _ago(db.now()) in ("now", "0s"), _ago(db.now())

    @check("a build's output goes into the log pane, not over the screen")
    def _():
        from snapforge.tui import Dashboard, DashboardReporter

        with tempfile.TemporaryDirectory() as home:
            board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
            reporter = DashboardReporter(board, None)
            # Unset, project.build hands the terminal over for the whole build.
            same(reporter.captures_output, True)

            reporter.output("Priming btop")
            assert any("Priming btop" in str(line) for line in board.log), \
                list(board.log)

            # The pane is a list of lines, so a 3-line note is 3 entries.
            before = len(board.log)
            reporter.detail("install it with:\n      snap install x\n      snap connect y")
            added = list(board.log)[before:]
            same(len(added), 3)
            assert not any("\n" in str(line) for line in added), added

    @check("the install question blocks the worker until a key answers it")
    def _():
        from snapforge.tui import Dashboard

        with tempfile.TemporaryDirectory() as home:
            board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))

            for keys, want in ((["y"], True), (["Y"], True), (["n"], False),
                               (["escape"], False), (["enter"], False)):
                out = {}
                board.run_job("building", lambda: out.update(
                    got=board._ask_yes_no("install x.snap?")))
                for _ in range(50):
                    if board.asking:
                        break
                    time.sleep(0.02)
                same(board.asking, "install x.snap?")
                for key in keys:
                    board.handle(key)
                for _ in range(50):
                    if "got" in out:
                        break
                    time.sleep(0.02)
                same(out.get("got"), want, f"{keys} answered wrongly")
                same(board.asking, "")

    @check("nothing else answers the install question by accident")
    def _():
        from snapforge.tui import Dashboard

        with tempfile.TemporaryDirectory() as home:
            board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
            out = {}
            board.run_job("building", lambda: out.update(
                got=board._ask_yes_no("install x.snap?")))
            for _ in range(50):
                if board.asking:
                    break
                time.sleep(0.02)

            # An arrow key used to read as a move, and a move is not an answer.
            for key in ("j", "k", "down", "up", "r", "b", "3"):
                board.handle(key)
            time.sleep(0.15)
            same("got" in out, False, "an unrelated key answered it")
            board.handle("n")
            for _ in range(50):
                if "got" in out:
                    break
                time.sleep(0.02)
            same(out.get("got"), False)

    @check("cancelling while the question is up counts as no, and does not hang")
    def _():
        from snapforge.tui import Dashboard

        with tempfile.TemporaryDirectory() as home:
            board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
            out = {}
            board.run_job("building", lambda: out.update(
                got=board._ask_yes_no("install x.snap?")))
            for _ in range(50):
                if board.asking:
                    break
                time.sleep(0.02)
            board.cancel.set()
            for _ in range(100):
                if "got" in out:
                    break
                time.sleep(0.02)
            same(out.get("got"), False, "cancelling did not answer it")

    @check("installing asks for root once, and only classic snaps get --classic")
    def _():
        import contextlib
        import types
        from snapforge import tui
        from snapforge.tui import Dashboard, DashboardReporter

        with tempfile.TemporaryDirectory() as home:
            here = Path(home)
            strict, classic = here / "a", here / "b"
            for path, confinement in ((strict, "strict"), (classic, "classic")):
                (path / "snap").mkdir(parents=True)
                (path / "snap" / "snapcraft.yaml").write_text(
                    f"name: x\nconfinement: {confinement}\n")

            board = Dashboard(db=db.Database(here / "snapkit.json"))
            reporter = DashboardReporter(board, None)
            board.suspended = lambda: contextlib.nullcontext()

            ran = []
            was = tui.subprocess
            tui.subprocess = types.SimpleNamespace(
                run=lambda argv, **kw: ran.append(argv)
                or types.SimpleNamespace(returncode=0))
            try:
                board._install(types.SimpleNamespace(name="x", path=str(strict)),
                               here / "x_1_amd64.snap")
                same(ran[-1], ["sudo", "snap", "install", "--dangerous",
                               str(here / "x_1_amd64.snap")])

                board._install(types.SimpleNamespace(name="x", path=str(classic)),
                               here / "x_1_amd64.snap")
                assert "--classic" in ran[-1], ran[-1]
            finally:
                tui.subprocess = was


# -- the updater, ported from the tool this replaced --------------------------

def updater():
    """Updating: resolving an upstream, and rewriting the project onto it."""
    from snapforge import build as buildlib
    from snapforge import db, project, rewrite, sources, update

    @check("every upstream shape is reachable by the name a record gives it")
    def _():
        same(sorted(sources.SHAPES),
             ["apt", "index", "local", "redirect", "tag-archive"])
        for bad in ("", "github", "ftp"):
            try:
                sources.resolve({"kind": bad})
                assert False, f"{bad!r} should not resolve"
            except sources.NetworkError:
                pass

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
        with patched(sources, get_text=lambda url, **k: index):
            found = sources.resolve({"kind": "apt", "base": "http://x",
                                     "package": "demo", "index": "http://x/P"})
        # The release, not the beta, and not the arm64 build of either.
        same(found.version, "1.11")
        same(found.sha, "ee")
        same(found.url, "http://x/pool/d/demo_1.11_amd64.deb")

        from snapforge import versions
        assert versions.deb_compare("1.11~beta.1", "1.11") < 0, "~ sorts first"
        assert versions.version_key("1.11~beta.1") > versions.version_key("1.11"), \
            "and sort -V is the ordering that would get this wrong"

    @check("deb_compare answers what dpkg answers, wherever dpkg can be asked")
    def _():
        # deb_compare saves a fork per comparison, while it still agrees.
        import shutil
        import subprocess
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
        import os
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

    @check("a wedged build container is recognised from snapcraft's own log")
    def _():
        with tempfile.TemporaryDirectory() as home:
            logs = Path(home) / "log"
            logs.mkdir()
            was = buildlib.SNAPCRAFT_LOGS
            buildlib.SNAPCRAFT_LOGS = logs
            try:
                same(buildlib.stale_instance(), "", "no logs, nothing to find")

                (logs / "old.log").write_text("nothing wrong here\n")
                same(buildlib.stale_instance(), "", "a clean log is not a wedge")

                # The CLI never sees snapcraft's output, so read its log.
                (logs / "new.log").write_text(
                    "Failed to add disk to instance 'snapcraft-demo-amd64-1234'.\n"
                    "* Command standard error output: b'Error: The device already exists'\n")
                same(buildlib.stale_instance(), "snapcraft-demo-amd64-1234")
            finally:
                buildlib.SNAPCRAFT_LOGS = was

    @check("a part is cleaned when the file under it is newer than the snap")
    def _():
        import os
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
        from snapforge.report import Reporter

        class Capture(Reporter):
            captures_output = True

            def __init__(self):
                self.lines = []

            def output(self, line):
                self.lines.append(line)

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
        from snapforge.report import Reporter

        class Capture(Reporter):
            captures_output = True

            def __init__(self):
                self.lines = []

            def output(self, line):
                self.lines.append(line)

        # Read as text, universal newlines split every \r redraw into a line.
        seen = Capture()
        buildlib.stream(["bash", "-c", r"printf '10%%\r50%%\r100%%\n'"], seen)
        same(seen.lines, ["100%"])

    @check("cancelling a build kills it rather than waiting for it")
    def _():
        import time
        from snapforge.report import Reporter

        class Stop(Exception):
            pass

        class Stopper(Reporter):
            captures_output = True

            def output(self, line):
                raise Stop()

        # Not KeyboardInterrupt: Popen.__exit__ special-cases it and gives up.
        started = time.time()
        try:
            buildlib.stream(["bash", "-c", "echo go; sleep 30"], Stopper())
            assert False, "should have raised"
        except Stop:
            pass
        assert time.time() - started < 5, "the child was waited on, not killed"


def from_a_file():
    """Packaging a file on disk, and keeping it in step with its folder."""
    from snapforge import classify, db, local, project, sources, update

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
        try:
            sources.resolve({"kind": "local", "glob": "*.deb"})
            assert False, "should have raised"
        except sources.NetworkError:
            pass

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


class Quiet:
    """A reporter that says nothing, for the tests that only want the result."""

    def step(self, text): pass
    def detail(self, text): pass
    def warn(self, text): pass
    def result(self, text): pass
    def progress(self, done, total): pass


class patched:
    """Swap module attributes for the length of a with-block."""

    def __init__(self, module, **names):
        self.module = module
        self.names = {k: v for k, v in names.items() if not k.startswith("_")}
        self.was = {}

    def __enter__(self):
        # apt_stanza reads through versions.get_text, imported into that module.
        from snapforge import versions
        self.versions_was = versions.get_text
        if "get_text" in self.names:
            versions.get_text = self.names["get_text"]
        for name, value in self.names.items():
            self.was[name] = getattr(self.module, name)
            setattr(self.module, name, value)
        return self

    def __exit__(self, *_):
        from snapforge import versions
        versions.get_text = self.versions_was
        for name, value in self.was.items():
            setattr(self.module, name, value)
        return False


# -- online -------------------------------------------------------------------

def online():
    from snapforge import classify, github

    @check("a release resolves, and something in it can be packaged")
    def _():
        for repo, kind in (("aristocratos/btop", "archive"),
                           ("sharkdp/bat", "deb"),
                           ("neovim/neovim", "archive")):
            release = github.release(repo)
            assert release.version, repo
            best = classify.classify(release.assets)
            assert best, f"{repo}: nothing packageable"
            same(best[0].kind, kind, repo)

    @check("a licence is read off the repository's own licence file")
    def _():
        for repo, want in (("aristocratos/btop", "Apache-2.0"),
                           ("neovim/neovim", "Apache-2.0"),
                           ("FreeTubeApp/FreeTube", "AGPL-3.0")):
            same(github.licence_of(repo), want, repo)

    @check("a repository that does not exist is an error, not a crash")
    def _():
        try:
            github.release("this-owner-does-not/exist-at-all-xyzzy")
            assert False, "should have raised"
        except (github.NotFound, github.NetworkError):
            pass


def _catch(board, plan, exception):
    """True if asking which asset raises `exception` -- for the escape case."""
    try:
        board._ask_which(plan())
        return False
    except exception:
        return True


def database():
    """The shared recipe database: what goes into it, and what comes back."""
    from snapforge import db, snapdb

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


def imports():
    """What the package imports of itself, and in which direction."""
    import ast
    import snapforge

    def imported_here(tree):
        """The sibling modules a file imports at module level, and no other."""
        found = set()

        def walk(node, in_function):
            for child in ast.iter_child_nodes(node):
                nested = in_function or isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                if not nested and isinstance(child, ast.ImportFrom) \
                        and child.level == 1:
                    if child.module:
                        found.add(child.module.split(".")[0])
                    else:
                        found.update(a.name for a in child.names)
                walk(child, nested)

        walk(tree, False)
        return found

    root = Path(snapforge.__file__).parent
    graph = {p.stem: imported_here(ast.parse(p.read_text(encoding="utf-8")))
             for p in sorted(root.glob("*.py")) if p.stem != "__init__"}

    @check("no module-level import in the package closes a cycle")
    def _():
        # A cycle is an ImportError, or worse a half-built module.
        def cycle_from(start):
            stack = [(start, [start])]
            seen = set()
            while stack:
                name, path = stack.pop()
                for other in sorted(graph.get(name, ())):
                    if other == start:
                        return path + [other]
                    if other in graph and (name, other) not in seen:
                        seen.add((name, other))
                        stack.append((other, path + [other]))
            return None

        for name in sorted(graph):
            found = cycle_from(name)
            assert found is None, " -> ".join(found)

    @check("the import graph read here is the one the package really has")
    def _():
        # An empty graph would pass the cycle test above for the wrong reason.
        assert "arch" in graph["classify"], "classify imports arch, and this missed it"
        assert "db" in graph["adopt"], "adopt imports db, and this missed it"
        assert "rewrite" in graph["recipe"], "recipe imports rewrite, and this missed it"
        # db reaches adopt inside a function, which is not an edge.
        assert "adopt" not in graph["db"], \
            "db imports adopt inside a function and this counted it anyway"
        for name, edges in sorted(graph.items()):
            for other in sorted(edges):
                assert other in graph, f"{name} imports a missing .{other}"




def dependencies():
    """What a payload needs at runtime, and which of it has to be staged."""
    from snapforge import depends, elf, platform, recipe

    @check("an ELF says what it needs without being run or resolved")
    def _():
        # /bin/sh is here on any machine this could run on, and readelf is not.
        needed, _soname, _paths = elf.read("/bin/sh")
        assert any(n.startswith("libc.so") for n in needed), needed
        assert all(n.endswith(".so") or ".so." in n for n in needed), needed
        assert elf.is_elf("/bin/sh") and not elf.is_elf("/etc/hostname")

    @check("a file that is not an ELF says so rather than being read as one")
    def _():
        with tempfile.TemporaryDirectory() as home:
            junk = Path(home) / "junk"
            junk.write_bytes(b"not an elf at all")
            try:
                elf.read(junk)
                assert False, "junk was read as an ELF"
            except elf.NotAnELF:
                pass

    @check("a Depends: field is read the way dpkg reads it")
    def _():
        same(depends.parse_depends("libfoo1 (>= 1.2), libbar2:amd64, "
                                   "kde-cli-tools | trash-cli, libbaz3"),
             ["libfoo1", "libbar2", "kde-cli-tools", "libbaz3"])
        # An alternative is a choice, and the first is what the packager meant.
        same(depends.parse_depends("a | b | c"), ["a"])
        same(depends.parse_depends(""), [])

    @check("noble renamed some packages, and a recipe that says otherwise fails")
    def _():
        # core24 has no libasound2, so the build stops at "no such package".
        same(depends.noble("libasound2"), "libasound2t64")
        same(depends.noble("libgbm1"), "libgbm1")
        assert "libasound2" in platform.RENAMED_T64

    @check("what the base and the extension already supply is never staged")
    def _():
        deb = {"Depends": "libgtk-3-0, libgbm1, libc6, tar, xdg-utils"}
        with_gnome = depends.resolve(control=deb, gui=True).packages
        without = depends.resolve(control=deb, gui=False).packages
        # gtk comes from the extension, so staging it as well is a conflict.
        assert "libgtk-3-0t64" not in with_gnome, with_gnome
        assert "libgtk-3-0t64" in without, without
        # The base has libc6 whatever happens, and tar and xdg-utils are tools.
        for both in (with_gnome, without):
            assert "libc6" not in both and "tar" not in both, both
            assert "libgbm1" in both, both

    @check("a library nothing can name is reported, never invented")
    def _():
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            # A binary needing something no table knows about.
            fake = "libnothingknowsthis.so.99"
            asked = {fake, "libc.so.6"}
            here = depends.supplied(gui=False)
            unknown = [s for s in asked
                       if s not in here and s not in platform.PACKAGE_OF]
            same(unknown, [fake])
            # It must not turn into a package name, because noble has none.
            assert fake not in platform.PACKAGE_OF

    @check("what a payload brings with it is not staged again")
    def _():
        needs = depends.Needs(packages=["libgbm1"], bundled=["libffmpeg.so"],
                              unresolved=[])
        assert needs.complete, "nothing was unaccounted for"
        assert depends.Needs(unresolved=["libx.so.1"]).complete is False

    @check("the recipe carries what was worked out, and no empty block")
    def _():
        with_packages = recipe.part_for("deb", "demo", "http://x/y.deb",
                                        packages=["libgbm1", "libnss3"])
        assert "stage-packages:" in with_packages
        assert "      - libgbm1\n" in with_packages, with_packages
        assert "      - libnss3" in with_packages, with_packages
        # Nothing to stage means no empty block, which snapcraft refuses.
        assert "stage-packages" not in recipe.part_for("deb", "demo", "u")

    @check("a Depends: full of daemons and tools stages neither")
    def _():
        # spotube's .deb asks for avahi-daemon, mpv and mdns-scan.
        deb = {"Depends": "avahi-daemon, mpv, mdns-scan, libnotify-bin, "
                          "gir1.2-appindicator3-0.1, xdg-user-dirs, "
                          "libjsoncpp1, libcairo-gobject2"}
        staged = depends.resolve(control=deb, gui=True).packages
        for tool in ("avahi-daemon", "mpv", "mdns-scan", "libnotify-bin",
                     "gir1.2-appindicator3-0.1", "xdg-user-dirs"):
            assert tool not in staged, f"{tool} was staged: {staged}"
        # Only the libraries survive, and cairo-gobject is the extension's.
        same(staged, ["libjsoncpp1"])
        assert "libcairo-gobject2" in depends.resolve(control=deb,
                                                      gui=False).packages

    @check("a name only the .deb vouches for is staged, and said to be unchecked")
    def _():
        # libmpv1 was right once; core24 has libmpv2 and would not build.
        needs = depends.resolve(control={"Depends": "libmpv1"}, gui=True)
        assert "libmpv1" in needs.packages, needs.packages
        assert "libmpv1" in needs.unverified, needs.unverified
        # One the platform vouches for is not flagged.
        known = depends.resolve(control={"Depends": "libgbm1"}, gui=True)
        same(known.unverified, [])

    @check("a driver's library comes from the host, so no package is named")
    def _():
        assert "libcuda.so.1" in platform.FROM_THE_HOST
        assert "libcuda.so.1" not in platform.PACKAGE_OF, \
            "staging a package for libcuda pins one driver version"


def main():
    for group in (upstreams, architectures, recipes, register, payloads,
                  reading_payloads, projects, checking, dashboard, updater,
                  from_a_file, database, tracking, dependencies, imports):
        group()
    if "--online" in sys.argv[1:]:
        online()
    for name, why in FAILED:
        print(f"FAIL  {name}\n        {why}")
    for name in PASSED:
        print(f"ok    {name}")
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
