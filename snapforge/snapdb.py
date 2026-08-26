"""The shared recipe database, published as a folder in a git repository.

A project here is more than its snapcraft.yaml. Three of the twenty-one build
from the recipe alone; the rest also need a launcher, an overlay tree, a
pack.py or a hook, and a recipe without them is a recipe that cannot build. So
what goes into the database is every source file of a project -- not the
downloaded release, not the built .snap, and not a build tree.

    snap-db/
        index.json              every snap, its version and its files
        btop/snapcraft.yaml
        btop/pack.py
        zen/overlay/bin/launcher
        ...

index.json is the whole of the protocol. A client reads it once, and then
knows what exists, what each one is, and which files to ask for -- so adding a
file to a project needs no new client, and a client that is a version behind
still works.

Publishing writes that tree out of the projects on this disk. Fetching reads
it back over https from raw.githubusercontent.com, which needs no token and no
git.
"""

import fnmatch
import json
import os
from pathlib import Path

from . import net

REPO = "bearenbey/snapkit"
BRANCH = "main"
FOLDER = "snap-db"
RAW = "https://raw.githubusercontent.com/{repo}/{branch}/{folder}"

INDEX = "index.json"
SCHEMA = 1

# What a pulled project cannot tell you about itself: where its release is.
RECORD = ("repo", "url", "kind", "version", "tag", "asset", "asset_pattern",
          "upstream", "style", "local_asset", "asset_glob", "source_anchor",
          "write_version", "checksums", "verify", "summary", "pack",
          "build_with", "icon")

# A project's own source, as opposed to a download or something a build made.
INCLUDE = ("snap/snapcraft.yaml", "snap/hooks/**", "snap/local/**",
           "snap/gui/**", "overlay/**", "launcher/**", "vendor/**",
           "pack.py", "diagnose.py", "launcher", "README.md")

# Downloads, build trees and outputs. A project directory holds all of these.
EXCLUDE = ("*.snap", "*.deb", "*.rpm", "*.AppImage", "*.tar.*", "*.tgz",
           "*.txz", "*.zip", "*.7z", "*.exe", "*.dmg", "*.pyc")
EXCLUDE_DIRS = ("prime", "parts", "stage", "work", "buildroot", "cache",
                "__pycache__", ".git")

# Above this a file is a payload rather than packaging, whatever it is called.
MAX_FILE = 1 << 20


class DatabaseError(Exception):
    """The database could not be read, or does not hold what was asked for."""


def base_url(repo=REPO, branch=BRANCH, folder=FOLDER):
    """Where the database is read from, or SNAPKIT_DB_URL when it is set.

    The override is what makes a fork, a private mirror or a local checkout
    usable without editing anything, and it is how the tests reach a database
    on disk instead of over the network.
    """
    return os.environ.get("SNAPKIT_DB_URL") or RAW.format(
        repo=repo, branch=branch, folder=folder)


# -- what belongs in the database ---------------------------------------------

def _wanted(relative):
    """Whether one path inside a project is packaging worth publishing."""
    text = str(relative)
    if any(part in EXCLUDE_DIRS for part in relative.parts):
        return False
    if any(fnmatch.fnmatch(relative.name, pattern) for pattern in EXCLUDE):
        return False
    return any(fnmatch.fnmatch(text, pattern) or text == pattern
               for pattern in INCLUDE)


def project_files(directory):
    """Every source file of one project, relative to it, sorted.

    Anything too large is left out and reported, so a vendored tarball does
    not quietly turn the database into a binary store.
    """
    directory = Path(directory)
    kept, skipped = [], []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(directory)
        if not _wanted(relative):
            continue
        if path.stat().st_size > MAX_FILE:
            skipped.append((relative, path.stat().st_size))
            continue
        kept.append(relative)
    return kept, skipped


def local_sources(recipe_text):
    """Every `source:` in a recipe that names a path rather than a URL."""
    found = []
    for line in recipe_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("source:"):
            continue
        value = stripped.split(":", 1)[1].strip().strip('"\'')
        if value and "://" not in value:
            found.append(value.lstrip("./"))
    return found


def unmet_sources(directory, kept, artifact=""):
    """Local sources a published project would arrive without.

    Driven by the recipe rather than by file size: the reason transmission
    cannot be built from the database is that its vendored gtkmm tarball is an
    archive, and archives are excluded as payload. Checking sizes would never
    have caught it -- checking what the recipe asks for does.

    The one release the project downloads is not counted: snapkit fetches that
    itself from the record.
    """
    directory = Path(directory)
    published = {str(k) for k in kept}
    unmet = []
    for source in local_sources((directory / "snap" / "snapcraft.yaml").read_text()):
        if source in (artifact, ".", ""):
            continue
        if source in published:
            continue
        if any(k.startswith(source.rstrip("/") + "/") for k in published):
            continue
        unmet.append(source)
    return unmet


# -- publishing ---------------------------------------------------------------

def publish(snaps, into, reporter=None):
    """Write the database out of the projects on this disk.

    `snaps` is an iterable of records. Returns the index it wrote.
    """
    into = Path(into)
    into.mkdir(parents=True, exist_ok=True)
    entries, left_out = {}, {}

    for snap in snaps:
        directory = Path(snap.path)
        if not (directory / "snap" / "snapcraft.yaml").is_file():
            continue
        kept, skipped = project_files(directory)
        unmet = unmet_sources(directory, kept, getattr(snap, "asset", ""))
        target = into / snap.name
        for relative in kept:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((directory / relative).read_bytes())
        entries[snap.name] = {
            "name": snap.name,
            "record": {field: getattr(snap, field) for field in RECORD
                       if getattr(snap, field, None) not in (None, "", {}, [])},
            "version": snap.version,
            "summary": getattr(snap, "summary", "") or "",
            "upstream": snap.repo or (snap.upstream or {}).get("kind", ""),
            "files": {
                str(r): {
                    "sha256": net.sha256_file(directory / r),
                    # Recorded, not guessed: snapd refuses a launcher with no +x
                    "exec": bool((directory / r).stat().st_mode & 0o111),
                }
                for r in kept
            },
            "pack": snap.pack or "",
        }
        missing = [str(r) for r, _ in skipped] + unmet
        if missing:
            left_out[snap.name] = missing
            entries[snap.name]["incomplete"] = missing
        if reporter:
            note = f"  (incomplete: {', '.join(missing)})" if missing else ""
            reporter.detail(f"{snap.name}: {len(kept)} files{note}")

    index = {"schema": SCHEMA, "snaps": entries}
    (into / INDEX).write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    return index, left_out


# -- fetching -----------------------------------------------------------------

def index(url=None):
    """The published index, read over https."""
    url = f"{url or base_url()}/{INDEX}"
    try:
        text = net.get_text(url)
    except net.NetworkError as exc:
        raise DatabaseError(f"could not read the database index: {exc}") from exc
    try:
        found = json.loads(text)
    except ValueError as exc:
        raise DatabaseError(f"{url} is not the index: {exc}") from exc
    if found.get("schema") != SCHEMA:
        raise DatabaseError(f"the database is schema {found.get('schema')} and "
                            f"this snapkit reads {SCHEMA} -- upgrade snapkit")
    return found


def entry(name, found=None, url=None):
    """One snap's index entry, or a DatabaseError naming what is there."""
    found = found or index(url)
    snaps = found.get("snaps", {})
    if name in snaps:
        return snaps[name]
    near = [k for k in sorted(snaps) if name in k]
    hint = f" -- did you mean {', '.join(near)}?" if near else ""
    raise DatabaseError(f"the database has no snap called {name}{hint}")


def fetch(name, into, found=None, url=None, reporter=None):
    """Download one snap's project files. Returns the directory written."""
    url = url or base_url()
    record = entry(name, found, url)
    into = Path(into)
    into.mkdir(parents=True, exist_ok=True)

    for relative, about in sorted(record.get("files", {}).items()):
        destination = into / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Bytes, not text: some of these are PNGs. The sha is free to check.
            net.download(f"{url}/{name}/{relative}", destination,
                         about.get("sha256", ""))
        except (net.NetworkError, OSError) as exc:
            raise DatabaseError(f"{name}: could not fetch {relative}: {exc}") from exc
        if about.get("exec"):
            destination.chmod(0o755)
        if reporter:
            reporter.detail(relative)

    if record.get("incomplete"):
        missing = ", ".join(record["incomplete"])
        raise DatabaseError(f"{name} is published without {missing}, which its "
                            f"build needs -- it cannot be built from the "
                            f"database")
    return into


def apply_record(snap, record):
    """Put the published update fields onto a record read off the project.

    Reading a project tells you what it builds; it cannot tell you where the
    release comes from or how an update reaches the packaging. That is what
    the index carries, and without it a pulled project has no artifact to
    build and no upstream to check.
    """
    for field, value in (record.get("record") or {}).items():
        if field in RECORD:
            setattr(snap, field, value)
    return snap
