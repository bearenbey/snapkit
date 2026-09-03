"""The shared recipe database, published as a folder in a git repository."""

import fnmatch
import hashlib
import json
import os
from pathlib import Path

from . import adopt, net

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


README = """# snap-db

Recipes for snaps that snapkit knows how to build, kept here as plain files so
a project packaged on one machine can be built on another without being worked
out again.

```sh
snapkit db                    what is in here
snapkit db pull               write every project into the current directory
snapkit db pull zen godot     just those
snapkit install zen           fetch it, build it, and offer to install it
```

There is no token, no login and no git involved. snapkit reads these files
over https from raw.githubusercontent.com.

## What a project looks like

A snap is more than its `snapcraft.yaml`. Three of them build from the recipe
alone. The rest also need a launcher, an overlay tree, a `pack.py` or a hook,
and a recipe without those is a recipe that will not build. So each project is
kept whole:

```
snap-db/
    index.json
    zen/
        snap/snapcraft.yaml
        pack.py
        overlay/bin/launcher
        overlay/meta/gui/zen.desktop
        overlay/opt/zen/distribution/policies.json
        README.md
```

Three things are deliberately missing: the release the project was built from,
the `.snap` it produced, and any build tree. snapkit downloads the release
itself, so there is no reason to keep a copy here.

## index.json

The index is the whole of the protocol. A client reads it once and then knows
what exists, what each one is and which files to ask for. Adding a file to a
project needs no new client, and a client a version behind still works.

```json
{
  "schema": 1,
  "snaps": {
    "zen": {
      "name": "zen",
      "version": "1.21.15b",
      "summary": "A calmer way to browse the web",
      "upstream": "zen-browser/desktop",
      "fingerprint": "9f86d081...",
      "record": { "style": "artifact",
                  "asset_glob": "zen.linux-x86_64.tar.xz" },
      "files": {
        "snap/snapcraft.yaml": { "sha256": "...", "exec": false },
        "overlay/bin/launcher": { "sha256": "...", "exec": true }
      }
    }
  }
}
```

`record` is the part a project cannot tell you about itself. Reading a project
says what it builds. It never says where the release comes from, or how an
update reaches the packaging, and without that a pulled project has nothing to
build from.

`sha256` is checked on the way in. `exec` is recorded rather than guessed from
the file name, because a launcher that arrives without its executable bit is a
snap snapd will refuse.

`fingerprint` covers every file in the project. `snapkit db` compares it
against what is on disk and marks anything that has moved on, so you can see
at a glance whether this folder still matches the projects it came from.

## Projects that cannot be published whole

If a recipe names a file too large to keep here, the project is marked
`incomplete` and `snapkit db pull <name>` refuses it by name and says which
file is missing. Pulling everything skips it and carries on rather than
stopping. Nothing is currently in that state.

## Publishing

Written straight out of the projects on a machine that has them:

```sh
snapkit db publish path/to/snap-db
```

This file is written by that command, so edit it in `snapforge/snapdb.py`
rather than here.

Point `SNAPKIT_DB_URL` at somewhere else to use a fork, a private mirror or a
checkout on disk.
"""


class DatabaseError(Exception):
    """The database could not be read, or does not hold what was asked for."""


def base_url(repo=REPO, branch=BRANCH, folder=FOLDER):
    """Where the database is read from, or SNAPKIT_DB_URL when it is set."""
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
    """Every source file of one project, relative to it, sorted."""
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
    """Local sources a published project would arrive without."""
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


def fingerprint(files):
    """One hash over a project's whole file list, for spotting drift."""
    digest = hashlib.sha256()
    for path, about in sorted(files.items()):
        digest.update(path.encode())
        digest.update(about["sha256"].encode())
        digest.update(b"1" if about.get("exec") else b"0")
    return digest.hexdigest()


def file_map(directory, kept):
    """What the index records per file: its hash, and its executable bit."""
    directory = Path(directory)
    return {str(relative): {"sha256": net.sha256_file(directory / relative),
                            "exec": bool((directory / relative).stat().st_mode
                                         & 0o111)}
            for relative in kept}


def local_fingerprint(directory):
    """The same hash, taken from a project on this disk."""
    kept, _ = project_files(directory)
    return fingerprint(file_map(directory, kept))


# -- publishing ---------------------------------------------------------------

def publish(snaps, into, reporter=None):
    """Write the database out of the projects on this disk."""
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
            "files": file_map(directory, kept),
            "pack": snap.pack or "",
        }
        entries[snap.name]["fingerprint"] = fingerprint(entries[snap.name]["files"])
        missing = [str(r) for r, _ in skipped] + unmet
        if missing:
            left_out[snap.name] = missing
            entries[snap.name]["incomplete"] = missing
        if reporter:
            note = f"  (incomplete: {', '.join(missing)})" if missing else ""
            reporter.detail(f"{snap.name}: {len(kept)} files{note}")

    index = {"schema": SCHEMA, "snaps": entries}
    (into / INDEX).write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    # Written here, so publishing into an empty directory is a whole database.
    (into / "README.md").write_text(README)
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
    # Before anything is written: half a project on disk helps nobody.
    if record.get("incomplete"):
        missing = ", ".join(record["incomplete"])
        raise DatabaseError(f"{name} is published without {missing}, which its "
                            f"build needs -- it cannot be built from the "
                            f"database")

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

    return into


def install(name, into, found=None, url=None, reporter=None, store=None):
    """Fetch one project out of the database and read it back as a record."""
    url = url or base_url()
    record = entry(name, found, url)
    fetch(name, into, {"snaps": {name: record}}, url, reporter)
    snap, recipe, is_snapcraft, _confirmed = adopt.read(into)
    adopt.take_icon(snap, into, store)
    apply_record(snap, record)
    return snap, recipe, is_snapcraft


def apply_record(snap, record):
    """Put the published update fields onto a record read off the project."""
    for field, value in (record.get("record") or {}).items():
        if field in RECORD:
            setattr(snap, field, value)
    return snap
