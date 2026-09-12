"""What every test module shares: the check, the fakes, and the fixtures."""

import contextlib
import io
import os
import tarfile
import time

from snapforge.report import Reporter

PASSED, FAILED = [], []


def check(name):
    """Decorator: run a function, and remember whether it raised."""
    def wrap(function):
        try:
            function()
            PASSED.append(name)
        except AssertionError as exc:
            FAILED.append((name, str(exc) or "assertion failed"))
        except Exception as exc:
            FAILED.append((name, f"{type(exc).__name__}: {exc}"))
        return function
    return wrap


def same(got, want, what=""):
    assert got == want, f"{what}: {got!r} != {want!r}"


def subprocess_result(returncode=0):
    """What a patched subprocess.run hands back."""
    return type("Result", (), {"returncode": returncode, "stdout": "",
                               "stderr": ""})()


# A reporter that says nothing, for the tests that only want the result.
Quiet = Reporter


class Capture(Reporter):
    """A reporter that keeps a subprocess's output, line by line."""

    captures_output = True

    def __init__(self):
        self.lines = []

    def output(self, line):
        self.lines.append(line)


def _raise(exception):
    """A stand-in that raises what it was given, however it is called."""
    def raiser(*_args, **_kwargs):
        raise exception
    return raiser


@contextlib.contextmanager
def patched(module, **names):
    """Swap module attributes for the length of a with-block."""
    was = {name: getattr(module, name) for name in names}
    for name, value in names.items():
        setattr(module, name, value)
    try:
        yield
    finally:
        for name, value in was.items():
            setattr(module, name, value)


@contextlib.contextmanager
def env(name, value):
    """One environment variable set for the length of a with-block."""
    was = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if was is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = was


@contextlib.contextmanager
def raises(exception, message=""):
    """A block that has to raise this; the failure says what did not."""
    try:
        yield
    except exception:
        return
    raise AssertionError(message or f"{exception} was not raised")


def wait_for(condition, tries=100, pause=0.02):
    """Poll for a worker thread's doing, for up to tries * pause seconds."""
    for _ in range(tries):
        if condition():
            return True
        time.sleep(pause)
    return False


def update_state(snap):
    from snapforge import update
    return update.situation(snap).state


def _catch(board, plan, exception):
    """True if asking which asset raises `exception` -- for the escape case."""
    try:
        board._ask_which(plan())
        return False
    except exception:
        return True


# -- a .deb, made here so the reader can be tested without the network --------


def make_deb(path, package="demo", version="1.2.3", binary="usr/bin/demo",
             control_name="./control"):
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
        info = tarfile.TarInfo(control_name)
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


# -- what a pack.py is handed --------------------------------------------------


def elf_needing(*sonames):
    """A 64-bit ELF whose dynamic section names these libraries, and no more."""
    import struct
    strtab, offsets = b"\0", []
    for soname in sonames:
        offsets.append(len(strtab))
        strtab += soname.encode() + b"\0"
    strtab_at = 64 + 2 * 56
    dynamic_at = strtab_at + len(strtab)
    entries = [(5, strtab_at)] + [(1, at) for at in offsets] + [(0, 0)]
    dynamic = b"".join(struct.pack("<QQ", *entry) for entry in entries)
    total = dynamic_at + len(dynamic)
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\0" * 8
    header = ident + struct.pack("<HHIQQQIHHHHHH", 3, 62, 1, 0, 64, 0, 0,
                                 64, 56, 2, 64, 0, 0)

    def segment(kind, offset, size):
        return struct.pack("<IIQQQQQQ", kind, 5, offset, offset, offset,
                           size, size, 0x1000)
    return (header + segment(1, 0, total) + segment(2, dynamic_at, len(dynamic))
            + strtab + dynamic)
