"""What a payload is opened for: the program to run, the icon to show,"""

import tempfile
from pathlib import Path

from snapforge import inspect

from .harness import check, same


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
