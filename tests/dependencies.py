"""What a payload needs at runtime, and which of it has to be staged."""

import tempfile
from pathlib import Path

from snapforge import depends, elf, platform, recipe

from .harness import check, same, raises, elf_needing


@check("an ELF says what it needs without being run or resolved")
def _():
    with tempfile.TemporaryDirectory() as home:
        binary = Path(home) / "app"
        binary.write_bytes(elf_needing("libc.so.6", "libm.so.6"))
        needed, _soname, _paths = elf.read(binary)
        same(needed, ["libc.so.6", "libm.so.6"])
        text = Path(home) / "notes.txt"
        text.write_text("not a binary")
        assert elf.is_elf(binary) and not elf.is_elf(text)


@check("a file that is not an ELF says so rather than being read as one")
def _():
    with tempfile.TemporaryDirectory() as home:
        junk = Path(home) / "junk"
        junk.write_bytes(b"not an elf at all")
        with raises(elf.NotAnELF, "junk was read as an ELF"):
            elf.read(junk)


@check("a file with the magic and nothing after it is refused, not read")
def _():
    # A truncated .so in a payload was an IndexError out of bundled_libraries.
    with tempfile.TemporaryDirectory() as home:
        stub = Path(home) / "libtruncated.so"
        stub.write_bytes(b"\x7fELF")
        with raises(elf.NotAnELF, "four bytes were read as an ELF"):
            elf.read(stub)
        same(depends.bundled_libraries(home), {})


@check("a Depends: field is read the way dpkg reads it")
def _():
    same(depends.parse_depends("libfoo1 (>= 1.2), libbar2:amd64, "
                               "kde-cli-tools | trash-cli, libbaz3"),
         ["libfoo1", "libbar2", "kde-cli-tools", "libbaz3"])
    # An alternative is a choice, and the first is what the packager meant.
    same(depends.parse_depends("a | b | c"), ["a"])
    same(depends.parse_depends(""), [])


@check("of two alternatives, the one core24 has is the one taken")
def _():
    # An old electron-builder .deb offers the dead name first.
    legacy = ("libgtk2.0-0, libudev0 | libudev1, "
              "libgcrypt11 | libgcrypt20, python | python3")
    chosen = depends.parse_depends(legacy)
    assert "libudev1" in chosen and "libudev0" not in chosen, chosen
    assert "libgcrypt20" in chosen and "libgcrypt11" not in chosen, chosen
    # libgcrypt11 has not existed for years, and naming it does not build.
    staged = depends.resolve(control={"Depends": legacy}, gui=True).packages
    for gone in ("libgcrypt11", "libudev0", "libgtk2.0-0"):
        assert gone not in staged, f"{gone} was staged: {staged}"
    # gtk2 is real, but noble spells it with the rename on the end.
    same(staged, ["libgtk2.0-0t64"])


@check("with nothing to choose between them, the packager's order stands")
def _():
    same(depends.parse_depends("kde-cli-tools | trash-cli"), ["kde-cli-tools"])
    same(depends.parse_depends("a | b | c"), ["a"])


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
        (root / "lib").mkdir()
        # What it ships for itself, what the base has, what a package has,
        # and something no table knows about.
        (root / "lib" / "libown.so.1").write_bytes(elf_needing("libc.so.6"))
        (root / "app").write_bytes(elf_needing(
            "libc.so.6", "libown.so.1", "libgbm.so.1",
            "libnothingknowsthis.so.99"))
        needs = depends.resolve(root=root, command="app", gui=False)
        assert "libgbm1" in needs.packages, needs.packages
        same(needs.unresolved, ["libnothingknowsthis.so.99"])
        assert needs.complete is False, "an unresolved library was glossed"
        assert not any("nothingknows" in p for p in needs.packages), (
            "it was turned into a package name, and noble has none")
        assert "libown.so.1" in needs.bundled, needs.bundled
        assert not any("libown" in p for p in needs.packages), (
            "what the payload brings with it was staged again")


@check("an Electron app is started through a wrapper, not from its binary")
def _():
    from snapforge import recipe
    # chrome-sandbox must be setuid root, which no snap file can be.
    yaml = recipe.build(name="min", version="1.0", summary="s",
                        description="d", license_id="", kind="deb",
                        url="http://x/y.deb", command="opt/Min/min",
                        traits={"gui", "electron"})
    assert "command: bin/min-launch" in yaml, yaml
    assert "min-launch: bin/min-launch" in yaml, "the wrapper is not staged"
    script = recipe.launcher_script("opt/Min/min")
    assert script.startswith("#!"), script
    assert 'exec "$SNAP/opt/Min/min"' in script, script
    # The namespace sandbox needs no setuid bit, so it is kept.
    assert "unshare --user" in script and "--no-sandbox" in script, script


@check("an app that is not Electron is started from its own binary")
def _():
    from snapforge import recipe
    yaml = recipe.build(name="btop", version="1.0", summary="s",
                        description="d", license_id="", kind="archive",
                        url="http://x/y.tar.gz", command="bin/btop",
                        traits={"terminal"})
    assert "command: bin/btop" in yaml, yaml
    assert "launcher" not in yaml, "a wrapper was written for no reason"


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
