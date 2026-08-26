"""Opening the downloaded payload to see what is actually in it.

The alternative is guessing, and guessing is wrong often enough to be useless:
a .deb from a Go project puts its binary in usr/bin, a .deb from an Electron
project puts it in opt/Name, and the desktop entry that names it is somewhere
else again. Since the payload has to be downloaded to build anyway, it is
opened first and the recipe is written from what is in it.

.deb is read here rather than shelled out to. A .deb is an `ar` archive of
three members, and the one that matters is a tar -- both of which Python can
read -- so the common case needs nothing installed. A payload compressed with
zstd falls back to dpkg-deb, which is the only piece Python has no reader for
on the base this snap is built against.
"""

import io
import re
import shutil
import stat
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# Best first: a binary under one of these is the app, elsewhere a helper.
BIN_DIRS = ("usr/bin", "bin", "usr/local/bin", "usr/games", "opt", "usr/lib")

# Files that are executable but are not the application.
NOT_THE_APP = re.compile(
    r"(uninstall|update|crashpad|crash_handler|chrome[-_]?sandbox|"
    r"chrome_crashpad|helper|postinst|prerm|postrm|preinst|\.so(\.|$)|"
    r"\.sh$|\.py$|\.pl$)", re.I)

ICON_SUFFIXES = (".png", ".svg", ".svgz", ".xpm", ".jpg")


@dataclass
class Payload:
    """What was found inside a downloaded release asset."""

    root: Path
    kind: str
    command: str = ""                  # relative path to the app binary
    desktop: str = ""                  # relative path to a .desktop entry
    icon: str = ""                     # relative path to the best icon
    version: str = ""                  # what the payload says it is
    summary: str = ""
    description: str = ""
    libraries: list = field(default_factory=list)   # unresolved NEEDED
    traits: set = field(default_factory=set)       # gui, electron, gtk, qt


class InspectionError(Exception):
    """The payload could not be opened, or held nothing recognisable."""


# -- getting the payload onto disk --------------------------------------------

def unpack(archive, destination, kind):
    """Extract an asset into `destination`, whatever shape it is in."""
    destination.mkdir(parents=True, exist_ok=True)
    if kind == "deb":
        _unpack_deb(archive, destination)
    elif kind == "appimage":
        _unpack_appimage(archive, destination)
    else:
        _unpack_archive(archive, destination)
    return destination


def _deb_member(path, prefix):
    """One member of a .deb, by name prefix, as (name, bytes).

    A .deb is an `ar` archive: a magic line, then for each member a 60-byte
    header of fixed-width text fields followed by its data, padded to an even
    length. Three members, and the two that matter here are both tars.
    """
    with open(path, "rb") as handle:
        if handle.read(8) != b"!<arch>\n":
            raise InspectionError(f"{path.name} is not a .deb (no ar magic)")
        while True:
            header = handle.read(60)
            if len(header) < 60:
                return "", b""
            name = header[0:16].decode("ascii", "replace").strip().rstrip("/")
            try:
                size = int(header[48:58].decode("ascii", "replace").strip())
            except ValueError as exc:
                raise InspectionError(f"{path.name}: damaged ar header") from exc
            if name.startswith(prefix):
                return name, handle.read(size)
            handle.seek(size + (size % 2), 1)


def _tar_bytes(blob):
    return tarfile.open(fileobj=io.BytesIO(blob), mode="r:*")


def _unpack_deb(archive, destination):
    name, blob = _deb_member(archive, "data.tar")
    if not name:
        raise InspectionError(f"{archive.name} has no data.tar member")
    if name.endswith(".zst"):
        # The one compression Python has no reader for on core24.
        if not shutil.which("dpkg-deb"):
            raise InspectionError(
                f"{archive.name} is zstd-compressed and dpkg-deb is not installed")
        subprocess.run(["dpkg-deb", "-x", str(archive), str(destination)], check=True)
        return
    with _tar_bytes(blob) as tar:
        tar.extractall(destination, filter="tar")


def _unpack_archive(archive, destination):
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(destination)
            # ZipFile drops the exec bit, so put it back or nothing will run.
            for info in zipped.infolist():
                mode = info.external_attr >> 16
                if mode & stat.S_IXUSR:
                    target = destination / info.filename
                    if target.is_file():
                        target.chmod(target.stat().st_mode | 0o111)
        return
    try:
        with tarfile.open(archive, mode="r:*") as tar:
            tar.extractall(destination, filter="tar")
    except tarfile.TarError as exc:
        raise InspectionError(f"{archive.name}: {exc}") from exc


def _unpack_appimage(archive, destination):
    """AppImages unpack themselves, and only themselves.

    An AppImage is an ELF launcher with a squashfs stuck on the end; the
    launcher knows the offset and nothing else does, so --appimage-extract is
    the way in. It needs the file to be executable and it writes into the
    working directory, hence the chmod and the cwd.
    """
    archive.chmod(archive.stat().st_mode | 0o111)
    done = subprocess.run([str(archive.resolve()), "--appimage-extract"],
                          cwd=destination, capture_output=True, text=True)
    extracted = destination / "squashfs-root"
    if done.returncode != 0 or not extracted.is_dir():
        raise InspectionError(
            f"{archive.name} would not extract: {done.stderr.strip()[:200]}")
    for item in extracted.iterdir():
        shutil.move(str(item), destination / item.name)
    extracted.rmdir()


# -- reading what came out ----------------------------------------------------

def control_fields(archive):
    """The .deb control stanza: Version, Description, Homepage and the rest."""
    name, blob = _deb_member(archive, "control.tar")
    if not name:
        return {}
    fields = {}
    with _tar_bytes(blob) as tar:
        try:
            member = tar.extractfile("./control") or tar.extractfile("control")
        except KeyError:
            return {}
        if member is None:
            return {}
        key = None
        for line in member.read().decode("utf-8", "replace").splitlines():
            if line[:1] in (" ", "\t") and key:
                fields[key] += "\n" + line.strip()
            elif ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                fields[key] = value.strip()
    return fields


def collapse_single_root(root):
    """The directory the payload's own paths are relative to.

    Nearly every tarball is one directory holding everything -- nvim-linux-
    x86_64/, btop/ -- and the snap wants what is inside it, not it. snapcraft
    does this itself for a `dump` part, so what matters here is knowing that
    it will, so the paths written into the recipe are the ones that will
    exist at build time.
    """
    entries = [e for e in root.iterdir() if e.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return root


def find_binaries(root):
    """Every executable file that could plausibly be the application."""
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if not path.stat().st_mode & stat.S_IXUSR:
            continue
        if NOT_THE_APP.search(relative):
            continue
        with open(path, "rb") as handle:
            if handle.read(4) != b"\x7fELF":
                continue
        found.append(relative)
    return found


def rank_binaries(binaries, wanted):
    """Order candidates so the application comes first.

    The name is the strongest signal -- a binary called `btop` in a repository
    called btop is the answer -- and after that it is a matter of where it
    sits: usr/bin before opt before anywhere else, and shallower before
    deeper, because the deep ones are bundled helpers.
    """
    def key(relative):
        path = Path(relative)
        stem = path.name.lower()
        target = (wanted or "").lower()
        exact = stem != target
        similar = not (target and target in stem)
        for index, directory in enumerate(BIN_DIRS):
            if relative.startswith(directory + "/"):
                place = index
                break
        else:
            place = len(BIN_DIRS)
        return (exact, similar, place, len(path.parts), relative)
    return sorted(binaries, key=key)


def find_desktop(root, wanted=""):
    """The .desktop entry, preferring one named after the application."""
    entries = [p.relative_to(root).as_posix()
               for p in sorted(root.rglob("*.desktop")) if p.is_file()]
    if not entries:
        return ""
    target = (wanted or "").lower()
    entries.sort(key=lambda r: (Path(r).stem.lower() != target,
                                target not in Path(r).stem.lower(),
                                len(Path(r).parts), r))
    return entries[0]


def find_icon(root, wanted=""):
    """The largest icon named after the application, or the largest icon."""
    icons = [p for p in root.rglob("*") if p.is_file()
             and p.suffix.lower() in ICON_SUFFIXES]
    if not icons:
        return ""
    target = (wanted or "").lower()

    def size_of(path):
        found = re.search(r"(\d+)x\d+", path.as_posix())
        # An SVG scales, so it beats every fixed size.
        return 10_000 if path.suffix.lower().startswith(".svg") else \
            int(found.group(1)) if found else 0

    icons.sort(key=lambda p: (p.stem.lower() != target,
                              target not in p.stem.lower(),
                              -size_of(p)))
    return icons[0].relative_to(root).as_posix()


# The toolkit decides what has to be plugged in for it to draw anything.
SIGNS = (
    ("electron", ("chrome-sandbox", "libffmpeg.so", "resources/app.asar",
                  "chrome_crashpad_handler", "libEGL.so")),
    ("qt", ("libQt5Core.so", "libQt6Core.so", "qt.conf")),
    ("gtk", ("libgtk-3.so", "libgtk-4.so", "libgdk-3.so")),
)


def is_terminal_app(root, desktop):
    """Whether the desktop entry asks to be run in a terminal.

    A .desktop file is not proof of a window: btop ships one and is a curses
    program. Terminal=true is upstream saying so, and it is the difference
    between a snap that pulls in the whole GTK stack and one that does not.
    """
    if not desktop:
        return False
    try:
        text = (root / desktop).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(r"(?mi)^Terminal\s*=\s*true\s*$", text))


def traits_of(root, desktop):
    """What the payload is, as far as it can be told from what is in it."""
    found = set()
    if desktop and not is_terminal_app(root, desktop):
        found.add("gui")
    if is_terminal_app(root, desktop):
        found.add("terminal")
    names = set()
    for path in root.rglob("*"):
        if path.is_file():
            names.add(path.name)
            names.add(path.relative_to(root).as_posix())
    for trait, markers in SIGNS:
        if any(marker in names or any(n.endswith(marker) for n in names)
               for marker in markers):
            found.add(trait)
    if found & {"electron", "qt", "gtk"} and "terminal" not in found:
        found.add("gui")
    return found


def missing_libraries(binary):
    """What ldd cannot resolve, which is what will fail at runtime."""
    if not shutil.which("ldd"):
        return []
    done = subprocess.run(["ldd", str(binary)], capture_output=True, text=True)
    return sorted({line.split()[0] for line in done.stdout.splitlines()
                   if "not found" in line})


def look(archive, kind, destination, wanted=""):
    """Unpack an asset and report what is in it."""
    unpack(archive, destination, kind)
    root = collapse_single_root(destination) if kind != "deb" else destination

    payload = Payload(root=root, kind=kind)
    binaries = rank_binaries(find_binaries(root), wanted)
    payload.command = binaries[0] if binaries else ""
    payload.desktop = find_desktop(root, wanted)
    payload.icon = find_icon(root, wanted)

    if kind == "deb":
        control = control_fields(archive)
        payload.version = control.get("Version", "")
        blurb = control.get("Description", "")
        payload.summary = blurb.splitlines()[0] if blurb else ""
        payload.description = "\n".join(blurb.splitlines()[1:]).strip()

    payload.traits = traits_of(root, payload.desktop)
    if payload.command:
        payload.libraries = missing_libraries(root / payload.command)
    return payload
