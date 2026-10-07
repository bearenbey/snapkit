"""Opening what was downloaded: the .deb reader, and what is inside."""

import tempfile
from pathlib import Path

from snapforge import inspect as ins

from .harness import check, same, raises, make_deb


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


@check("a control file stored without ./ is still read")
def _():
    # extractfile raises for a missing name, so the fallback never ran.
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        deb = make_deb(work / "demo_1.2.3_amd64.deb", control_name="control")
        same(ins.control_fields(deb).get("Version"), "1.2.3")


@check("a .deb whose data.tar is broken is refused, not a traceback")
def _():
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        deb = make_deb(work / "demo_1.2.3_amd64.deb")
        raw = deb.read_bytes()
        at = raw.index(b"data.tar.gz")
        body = at + 60           # past the ar header, into the gzip stream
        deb.write_bytes(raw[:body] + b"\0" * 16 + raw[body + 16:])
        with raises(ins.InspectionError, "a corrupt archive was read"):
            ins.look(deb, "deb", work / "out", wanted="demo")


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
        with raises(ins.InspectionError, "should have raised"):
            ins.look(fake, "deb", Path(work) / "out")


def _tar_of(path, members):
    """A tar with these (name, kind, data-or-target) members, in order."""
    import io
    import tarfile
    with tarfile.open(path, "w:gz") as tar:
        for name, kind, what in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.mode = 0o755
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = what
                tar.addfile(info)
            elif kind == tarfile.DIRTYPE:
                tar.addfile(info)
            else:
                info.size = len(what)
                tar.addfile(info, io.BytesIO(what))


@check("a link in a payload cannot point at the host")
def _():
    # The "tar" filter checks where a member lands, not where a link points,
    # and find_icon follows links: app.png -> /etc/hostname was copied in.
    import os
    import tarfile as tf
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        archive = work / "demo-1.0.tar.gz"
        _tar_of(archive, [
            ("usr", tf.DIRTYPE, None), ("usr/bin", tf.DIRTYPE, None),
            ("usr/share", tf.DIRTYPE, None),
            ("usr/share/real", tf.REGTYPE, b"hello"),
            # What a .deb does: absolute, meaning the tree it unpacks into.
            ("usr/bin/code", tf.SYMTYPE, "/usr/share/real"),
            ("app.png", tf.SYMTYPE, "/etc/hostname"),
            ("app.desktop", tf.SYMTYPE, "../../../../etc/passwd"),
            ("passwd", tf.LNKTYPE, "/etc/passwd"),
        ])
        out = work / "out"
        ins.unpack(archive, out, "archive")
        same((out / "usr/bin/code").read_text(), "hello", "the .deb style link")
        same(os.readlink(out / "app.png"), "etc/hostname", "re-rooted")
        assert not (out / "app.png").exists(), "it must dangle inside the tree"
        for name in ("app.desktop", "passwd"):
            assert not os.path.lexists(out / name), f"{name} was extracted"

    # The same for a tree dpkg-deb or an AppImage left behind.
    with tempfile.TemporaryDirectory() as work:
        tree = Path(work)
        (tree / "usr/share").mkdir(parents=True)
        (tree / "usr/share/x").write_text("x")
        os.symlink("/usr/share/x", tree / "ok")
        os.symlink("../../../..", tree / "up")
        ins._tame_links(tree)
        same((tree / "ok").read_text(), "x")
        assert not os.path.lexists(tree / "up"), "a link out of the tree stayed"


@check("a damaged zip, ar header or tar is refused, not a traceback")
def _():
    import zipfile
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        bad = work / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zipped:
            zipped.writestr("a.txt", "abc")
        raw = bytearray(bad.read_bytes())
        raw[raw.index(b"abc")] = ord("x")          # is_zipfile reads only the end
        bad.write_bytes(raw)
        with raises(ins.InspectionError, "a bad crc was not refused"):
            ins.unpack(bad, work / "zip", "archive")

        deb = work / "neg.deb"                     # seek(-60) looped for ever
        header = b"debian-binary   " + b"0" * 12 + b"0     " * 2 + b"100644  "
        deb.write_bytes(b"!<arch>\n" + header + b"-60       " + b"`\n")
        with raises(ins.InspectionError, "a negative ar size was not refused"):
            ins._deb_member(deb, "data.tar")

        import tarfile as tf
        tar = work / "notdir.tar.gz"               # OSError is not a TarError
        _tar_of(tar, [("app", tf.REGTYPE, b"x"), ("app/inner", tf.REGTYPE, b"y")])
        with raises(ins.InspectionError, "a file where a dir was promised"):
            ins.unpack(tar, work / "tar", "archive")
