"""Reading an upstream: the repository name, and which asset to take."""

import contextlib
import tempfile
from pathlib import Path

from snapforge import classify, github, project

from .harness import check, same, patched, raises


@check("a download that dies after the connection opened is still a NetworkError")
def _():
    # Only the connect was guarded: a stall mid-body was a bare TimeoutError.
    from snapforge import net
    import http.client

    def failing(exc):
        @contextlib.contextmanager
        def _open(opener, url, **kw):
            class Response:
                headers = {}
                def read(self, *a):
                    raise exc
            yield Response()
        return _open

    with tempfile.TemporaryDirectory() as here:
        for exc in (TimeoutError("stalled"), ConnectionResetError(),
                    http.client.IncompleteRead(b"")):
            with patched(net, _open=failing(exc)):
                for call in (lambda: net.get_text("https://h/x"),
                             lambda: net.download("https://h/x", Path(here) / "x")):
                    with raises(net.NetworkError, f"{exc!r} was not raised"):
                        call()
            assert not (Path(here) / "x.part").exists(), "a .part was left"


@check("github.parse_repo takes a url in any of its shapes")
def _():
    for text in ("imputnet/helium-linux",
                 "https://github.com/imputnet/helium-linux",
                 "https://github.com/imputnet/helium-linux/",
                 "git@github.com:imputnet/helium-linux.git",
                 "github.com/imputnet/helium-linux/releases/tag/0.1",
                 "https://github.com/imputnet/helium-linux.git?tab=readme"):
        same(github.parse_repo(text), "imputnet/helium-linux", text)
    for bad in ("", "not a url", "https://gitlab.com/a/b",
                "https://github.com/torvalds"):
        with raises(ValueError, f"{bad!r} should not parse"):
            github.parse_repo(bad)


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


@check("a tag written in something other than ascii survives the round trip")
def _():
    # A byte at a time turned the utf-8 in v1.0-ä into mojibake.
    same(github._unquote("v1.0-%C3%A4"), "v1.0-\u00e4")
    same(github._unquote("v1.0%20final"), "v1.0 final")
    # A description carries more entities than the six that were listed.
    same(github._unescape("Tom&#x27;s tool &amp; more &#8212; nice"),
         "Tom\u0027s tool & more \u2014 nice")


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
