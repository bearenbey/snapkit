"""Writing a snapcraft.yaml, and moving one onto a newer release."""

import subprocess
import tempfile
from pathlib import Path

from snapforge import classify, db, recipe

from .harness import check, same


@check("recipe.snap_name produces a name snapd will take")
def _():
    for text, want in (("helium-linux", "helium"), ("FreeTube", "freetube"),
                       ("my_cool_app", "my-cool-app"), ("signal-desktop", "signal"),
                       ("draw.io-desktop", "draw-io"), ("2fa", "s-2fa"),
                       # the s- prefix pushed a hyphen onto the end
                       ("1" * 37 + "-abc", "s-" + "1" * 37)):
        same(recipe.snap_name(text), want, text)


@check("a summary YAML would read as something else is quoted")
def _():
    # GitHub's own description is written in: "Helium: a browser" broke it.
    for summary, want in (("a demo", "summary: a demo"),
                          ("Helium: a browser", 'summary: "Helium: a browser"'),
                          ("**Fast** tool", 'summary: "**Fast** tool"'),
                          ("[WIP] thing", 'summary: "[WIP] thing"'),
                          ("hello # world", 'summary: "hello # world"'),
                          ("it's fine", "summary: it's fine"),
                          ("yes", 'summary: "yes"')):
        text = recipe.build(name="d", version="1", summary=summary,
                            description="b", license_id="", kind="archive",
                            url="u", command="bin/d", traits={"terminal"})
        assert want in text, f"{summary!r}: {text.splitlines()[5]}"


@check("the readers take a field, a block and a command off recipe text")
def _():
    text = ("name: demo\nversion: '1.2'\nsummary: \"a: thing\"\n"
            "description: |\n  first line\n\n  third line\nconfinement: classic\n"
            "apps:\n  demo:\n    command: bin/demo\n"
            "parts:\n  demo:\n    source: https://h/demo.deb\n")
    same(recipe.field(text, "version"), "1.2")
    same(recipe.field(text, "summary"), "a: thing", "quotes are stripped")
    same(recipe.field(text, "grade"), "", "an absent field is empty")
    same(recipe.block(text, "description"), "first line\n\nthird line")
    same(recipe.block(text, "summary"), "a: thing", "a one-line value")
    same(recipe.first_command(text), "bin/demo")
    same(recipe.sources(text), [("demo", "https://h/demo.deb")])
    # `source:` only counts inside parts:, and only under a part name.
    same(recipe.sources("source: x\nparts:\n  source: y\n"), [])


@check("metadata_file prefers the overlay's snap.yaml, and is_classic reads it")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        same(recipe.metadata_file(here), None, "nothing there yet")
        assert not recipe.is_classic(here)
        (here / "snap").mkdir()
        (here / "snap" / "snapcraft.yaml").write_text("confinement: strict\n")
        same(recipe.metadata_file(here), here / recipe.SNAPCRAFT_YAML)
        assert not recipe.is_classic(here)
        (here / "overlay" / "meta").mkdir(parents=True)
        (here / recipe.META_YAML).write_text("confinement: classic\n")
        same(recipe.metadata_file(here), here / recipe.META_YAML,
             "the assembled tree's own metadata wins")
        assert recipe.is_classic(here)
        same(recipe.field_of(here / "nowhere.yaml", "name"), "",
             "a missing file reads as empty, not as an error")


@check("a recipe with no url to repoint is left alone, not shredded")
def _():
    # "" is in every string, so every source: line was rewritten around it.
    text = "name: d\nversion: '1.0'\nparts:\n  d:\n    source: .\n"
    after = recipe.repoint(text, "1.0", "2.0", "", "https://h/new.deb")
    assert "    source: .\n" in after, after


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
    text = recipe.build(name="d", version="1", summary="s", description="b",
                        license_id="", kind=classify.APPIMAGE,
                        url="https://x/d.appimage", command="usr/bin/d")
    line = next(line.strip() for line in text.splitlines()
                if line.strip().startswith("image=$("))
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
