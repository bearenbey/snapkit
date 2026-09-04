"""Opening the downloaded payload to see what is actually in it."""

import io
import os
import re
import shutil
import stat
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import elf

# Best first: a binary under one of these is the app, elsewhere a helper.
BIN_DIRS = ("usr/bin", "bin", "usr/local/bin", "usr/games", "opt", "usr/lib")

# Files that are executable but are not the application.
NOT_THE_APP = re.compile(
    r"(uninstall|update|crashpad|crash_handler|chrome[-_]?sandbox|"
    r"chrome_crashpad|helper|postinst|prerm|postrm|preinst|\.so(\.|$)|"
    r"\.sh$|\.py$|\.pl$)", re.I)

# The executable scripts a tree carries to build itself with, never the app.
BUILD_HELPER = re.compile(
    r"(?:^|/)(?:configure|config\.guess|config\.sub|config\.status|compile|"
    r"depcomp|install-sh|missing|ylwrap|ltmain\.sh|libtool|mkinstalldirs|"
    r"test-driver|ar-lib|py-compile|autogen|bootstrap|gradlew|waf)$", re.I)

# What a tree that has to be built first looks like, and what builds it.
BUILD_SYSTEMS = (
    ("autotools", ("configure.ac", "configure.in", "Makefile.am", "configure")),
    ("cmake", ("CMakeLists.txt",)),
    ("meson", ("meson.build",)),
    ("cargo", ("Cargo.toml",)),
    ("go", ("go.mod",)),
    ("make", ("Makefile", "GNUmakefile")),
)

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
    builds_with: str = ""              # set when this is source, not a build


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
    """One member of a .deb, by name prefix, as (name, bytes)."""
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


def _extract_all(tar, destination):
    """extractall, refusing a member that would land outside `destination`."""
    # filter= is why the floor is 3.10.12: it is where the keyword arrived.
    tar.extractall(destination, filter="tar")


def _unpack_deb(archive, destination):
    name, blob = _deb_member(archive, "data.tar")
    if not name:
        raise InspectionError(f"{archive.name} has no data.tar member")
    if name.endswith(".zst"):
        # The one compression Python has no reader for on core24.
        if not shutil.which("dpkg-deb"):
            raise InspectionError(
                f"{archive.name} is zstd-compressed and dpkg-deb is not installed")
        done = subprocess.run(["dpkg-deb", "-x", str(archive), str(destination)],
                              capture_output=True, text=True)
        if done.returncode != 0:
            raise InspectionError(
                f"{archive.name} would not unpack: {done.stderr.strip()[:200]}")
        return
    try:
        with _tar_bytes(blob) as tar:
            _extract_all(tar, destination)
    except tarfile.TarError as exc:
        raise InspectionError(f"{archive.name}: {exc}") from exc


def _unpack_archive(archive, destination):
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(destination)
            # ZipFile drops the exec bit, so put it back or nothing will run.
            for info in zipped.infolist():
                mode = info.external_attr >> 16
                if not mode & stat.S_IXUSR:
                    continue
                # Where extractall put it, not what the entry called itself.
                target = _extracted_as(destination, info.filename)
                if target is not None and target.is_file():
                    target.chmod(target.stat().st_mode | 0o111)
        return
    try:
        with tarfile.open(archive, mode="r:*") as tar:
            _extract_all(tar, destination)
    except tarfile.TarError as exc:
        raise InspectionError(f"{archive.name}: {exc}") from exc


def _extracted_as(destination, name):
    """Where ZipFile put an entry, which drops the parts that would escape."""
    parts = [p for p in name.replace("\\", "/").split("/")
             if p not in ("", os.curdir, os.pardir)]
    return destination.joinpath(*parts) if parts else None


def _unpack_appimage(archive, destination):
    """AppImages unpack themselves, and only themselves."""
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

def _control_text(archive, blob):
    """The control file out of control.tar, or None if nothing can read it."""
    try:
        with _tar_bytes(blob) as tar:
            for name in ("./control", "control"):
                try:
                    member = tar.extractfile(name)
                except KeyError:
                    continue
                if member is not None:
                    return member.read().decode("utf-8", "replace")
            return None
    except tarfile.TarError:
        pass
    # Usually a zstd control.tar on a Python without a reader for it.
    if not shutil.which("dpkg-deb"):
        return None
    done = subprocess.run(["dpkg-deb", "-f", str(archive)],
                          capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None

def control_fields(archive):
    """The .deb control stanza: Version, Description, Homepage and the rest."""
    name, blob = _deb_member(archive, "control.tar")
    if not name:
        return {}
    text = _control_text(archive, blob)
    if text is None:
        return {}
    fields, key = {}, None
    for line in text.splitlines():
        if line[:1] in (" ", "\t") and key:
            fields[key] += "\n" + line.strip()
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            fields[key] = value.strip()
    return fields


def collapse_single_root(root):
    """The directory the payload's own paths are relative to."""
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
        if NOT_THE_APP.search(relative) or BUILD_HELPER.search(relative):
            continue
        if not _is_program(path):
            continue
        found.append(relative)
    return found


def _is_program(path):
    """A compiled binary, or a script that says what runs it."""
    with open(path, "rb") as handle:
        head = handle.read(4)
    return head[:4] == b"\x7fELF" or head[:2] == b"#!"


def build_system(root):
    """Which build system a source tree uses, or "" when it is not one."""
    for name, markers in BUILD_SYSTEMS:
        if any((Path(root) / marker).is_file() for marker in markers):
            return name
    return ""


def anything_compiled(root):
    """Whether the tree holds a compiled program or library at all."""
    for path in Path(root).rglob("*"):
        if path.is_file() and not path.is_symlink() and elf.is_elf(path):
            return True
    return False


def source_only(root):
    """The build system of a tree that has to be built, or "" if it is built."""
    system = build_system(root)
    # Source trees carry stray scripts, so nothing compiled is what settles it.
    return system if system and not anything_compiled(root) else ""


def rank_binaries(binaries, wanted):
    """Order candidates so the application comes first."""
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


def desktop_icon(root, desktop):
    """The icon a .desktop entry asks for by name, which is the authority."""
    if not desktop:
        return ""
    try:
        text = (root / desktop).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    found = re.search(r"(?mi)^Icon\s*=\s*(.+?)\s*$", text)
    if not found:
        return ""
    # Strip only a real suffix: Path.stem makes net.lutris.Lutris net.lutris.
    name = Path(found.group(1).strip()).name
    stem, dot, suffix = name.rpartition(".")
    if dot and f".{suffix.lower()}" in ICON_SUFFIXES:
        name = stem
    return name.lower()


def find_icon(root, wanted="", named=""):
    """The icon the entry names, else the biggest one called after the app."""
    icons = [p for p in root.rglob("*") if p.is_file()
             and p.suffix.lower() in ICON_SUFFIXES]
    if not icons:
        return ""
    target = (wanted or "").lower()
    named = (named or "").lower()

    def size_of(path):
        # An SVG scales, so it beats every fixed size.
        if path.suffix.lower().startswith(".svg"):
            return 10_000
        found = re.search(r"(\d+)x\d+", path.as_posix())
        return int(found.group(1)) if found else 0

    def not_an_app_icon(path):
        # In hicolor, anything outside apps/ is a file type or a status glyph.
        where = path.as_posix().lower()
        return "/apps/" not in where and "/mimetypes/" in where

    icons.sort(key=lambda p: (not (named and p.stem.lower() == named),
                              not_an_app_icon(p),
                              p.stem.lower() != target,
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
    """Whether the desktop entry asks to be run in a terminal."""
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
    terminal = is_terminal_app(root, desktop)
    if terminal:
        found.add("terminal")
    elif desktop:
        found.add("gui")
    names = set()
    for path in root.rglob("*"):
        if path.is_file():
            names.add(path.name)
            names.add(path.relative_to(root).as_posix())
    for trait, markers in SIGNS:
        if any(marker in names or any(n.endswith(marker) for n in names)
               for marker in markers):
            found.add(trait)
    if found & {"electron", "qt", "gtk"} and not terminal:
        found.add("gui")
    return found


def bundled_lib_dirs(root):
    """Where inside a payload a portable build ships its own libraries."""
    found = []
    for name in ("lib", "lib64", "usr/lib", "libs", "bin"):
        where = root / name
        if where.is_dir() and any(where.glob("*.so*")):
            found.append(where)
    for where in sorted(root.glob("*/*")):
        if where.is_dir() and where.name in ("lib", "lib64") \
                and any(where.glob("*.so*")):
            found.append(where)
    return found


def missing_libraries(binary, root=None):
    """What ldd cannot resolve, looking in the payload's own lib dirs too."""
    if not shutil.which("ldd"):
        return []
    environment = dict(os.environ)
    if root is not None:
        bundled = [str(d) for d in bundled_lib_dirs(Path(root))]
        if bundled:
            was = environment.get("LD_LIBRARY_PATH", "")
            environment["LD_LIBRARY_PATH"] = os.pathsep.join(
                bundled + ([was] if was else []))
    done = subprocess.run(["ldd", str(binary)], capture_output=True, text=True,
                          env=environment)
    return sorted({line.split()[0] for line in done.stdout.splitlines()
                   if "not found" in line})


def launcher_among(root, candidates):
    """A script that runs another candidate, which makes it the one to run."""
    for relative in candidates:
        path = root / relative
        try:
            with open(path, "rb") as handle:
                if handle.read(2) != b"#!":
                    continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for other in candidates:
            if other != relative and other in text:
                return relative
    return ""


def look(archive, kind, destination, wanted=""):
    """Unpack an asset and report what is in it."""
    unpack(archive, destination, kind)
    root = collapse_single_root(destination) if kind != "deb" else destination

    payload = Payload(root=root, kind=kind)
    binaries = rank_binaries(find_binaries(root), wanted)
    # A script that runs the binary is the thing to run, not the binary.
    payload.command = launcher_among(root, binaries) or (
        binaries[0] if binaries else "")
    payload.desktop = find_desktop(root, wanted)
    payload.icon = find_icon(root, wanted,
                             named=desktop_icon(root, payload.desktop))

    if kind == "deb":
        control = control_fields(archive)
        payload.version = control.get("Version", "")
        blurb = control.get("Description", "")
        payload.summary = blurb.splitlines()[0] if blurb else ""
        payload.description = "\n".join(blurb.splitlines()[1:]).strip()

    payload.builds_with = source_only(root)
    if payload.builds_with:
        # Whatever turned up executable in there, it is not the application.
        payload.command = ""
    payload.traits = traits_of(root, payload.desktop)
    if payload.command:
        payload.libraries = missing_libraries(root / payload.command, root)
    return payload
