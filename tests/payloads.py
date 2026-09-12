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
