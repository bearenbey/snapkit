"""The register of every snap this tool has made."""

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

# How many builds of one snap to keep the detail of.
HISTORY_KEPT = 20

# The field order, so a hand edit and a rewrite do not diff the whole file.
ORDER = ("name", "repo", "url", "upstream", "kind", "style", "version", "tag",
         "asset", "asset_pattern", "local_asset", "asset_glob", "source_anchor",
         "write_version", "checksums", "verify", "summary", "description", "license",
         "confinement", "grade", "base", "command", "pack", "build_with",
         "icon", "plugs", "directory", "created", "updated", "builds",
         "history")


def home():
    """Where the register and the generated projects live."""
    for name in ("SNAPKIT_HOME", "SNAP_USER_COMMON"):
        value = os.environ.get(name)
        if value:
            return Path(value)
    xdg = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(xdg).expanduser() / "snapkit"


def default_path():
    """The register directory. The name is historical: it used to be a file."""
    return home()


@dataclass
class Snap:
    """One registered snap."""

    name: str
    repo: str = ""                  # owner/name on GitHub
    url: str = ""                   # the repository page
    kind: str = ""                  # deb | archive | appimage
    version: str = ""
    tag: str = ""
    asset: str = ""                 # the file this was built from
    asset_pattern: str = ""         # how to find that file next release
    # A non-GitHub upstream; empty means `repo` above. See sources.py.
    upstream: dict = field(default_factory=dict)
    # recipe: snapcraft fetches the source. artifact: the file sits here.
    style: str = ""
    local_asset: str = ""           # artifact: what the build opens it as
    asset_glob: str = ""            # artifact: matches every version's file
    # Which source: line to repoint, so another part's is left alone.
    source_anchor: str = ""
    # recipe: a version: field snapcraft does not adopt from the source.
    write_version: bool = False
    # Where the checksum lives, when it is not published beside the release.
    checksums: dict = field(default_factory=dict)
    # What the download is checked against before its checksum is trusted.
    verify: dict = field(default_factory=dict)
    summary: str = ""
    description: str = ""
    license: str = ""
    confinement: str = "strict"
    grade: str = "stable"
    base: str = "core24"
    command: str = ""
    # The file exposing build(p), which `snapkit build` imports and calls.
    pack: str = ""
    build_with: str = ""            # a command, when neither of those is the way
    icon: str = ""                   # relative to the project directory
    plugs: list = field(default_factory=list)
    directory: str = ""
    created: str = ""
    updated: str = ""
    history: list = field(default_factory=list)   # the last few, in full
    builds: int = 0                              # how many there have been

    # None is "not read yet"; an imported project genuinely has no recipe.
    recipe_text: str | None = None

    # Which register this came from; not written down, it is where it was found.
    store_root: Path | None = None

    # Where it was read from, when that is not where it would be written.
    record_file: str | None = None

    @property
    def store(self):
        return self.store_root or home()

    @property
    def snapcraft_yaml(self):
        """The recipe, read from its own file the first time it is asked for."""
        if self.recipe_text is None:
            path = self.recipe_path
            try:
                self.recipe_text = path.read_text(encoding="utf-8")
            except OSError:
                self.recipe_text = ""
        return self.recipe_text

    @snapcraft_yaml.setter
    def snapcraft_yaml(self, text):
        self.recipe_text = text or ""

    @property
    def recipe_path(self):
        return self.store / "recipes" / f"{self.name}.yaml"

    @classmethod
    def from_dict(cls, data):
        known = {f.name for f in fields(cls)}
        taken = {k: v for k, v in data.items() if k in known}
        # The single-file register kept the recipe inline under its old name.
        if "snapcraft_yaml" in data and "recipe_text" not in data:
            taken["recipe_text"] = data["snapcraft_yaml"]
        return cls(**taken)

    def to_dict(self):
        """The record as it is written down -- the recipe is not part of it."""
        raw = asdict(self)
        raw.pop("recipe_text", None)
        raw.pop("store_root", None)
        raw.pop("record_file", None)
        ordered = {key: raw[key] for key in ORDER if key in raw}
        ordered.update({k: v for k, v in raw.items() if k not in ordered})
        return ordered

    def record_build(self, version, at=None):
        """Note that this version was built, most recent last."""
        stamp = at or now()
        self.history.append({"version": version, "at": stamp})
        del self.history[:-HISTORY_KEPT]
        self.builds += 1
        self.version = version
        self.updated = stamp

    @property
    def path(self):
        if self.directory:
            return Path(self.directory)
        return self.store / "projects" / self.name

    @property
    def kept_icon(self):
        """The copy of the icon held next to the register, if there is one."""
        if not self.icon:
            return None
        kept = self.store / "icons" / (self.name + Path(self.icon).suffix)
        return kept if kept.is_file() else None

    def keep_icon(self, source):
        """Put a copy of the icon beside the register and return where."""
        kept = self.store / "icons" / (self.name + Path(source).suffix)
        kept.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, kept)
        return kept


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    """The register directory, and the snaps in it."""

    def __init__(self, path=None):
        # A path ending in .json is taken as the old single-file register.
        given = Path(path) if path else default_path()
        self.root = given.parent if given.suffix == ".json" else given
        self.snaps = {}
        self.problems = []        # records that could not be read
        self.resynced = []        # records whose version was stale on disk
        self.load()

    # -- where things are ----------------------------------------------------

    @property
    def path(self):
        return self.root

    def record_path(self, name):
        return self.root / "snaps" / f"{name}.json"

    # -- reading -------------------------------------------------------------

    def load(self):
        """Read every record. Recipes are left on disk until wanted."""
        self.migrate()
        self.snaps = {}
        self.problems = []
        self.resynced = []        # [(name, was, now)] corrected by this load
        folder = self.root / "snaps"
        if not folder.is_dir():
            return self
        for record in sorted(folder.glob("*.json")):
            try:
                data = json.loads(record.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                self.problems.append((record, str(exc)))
                continue
            if not isinstance(data, dict):
                self.problems.append((record, "not a record"))
                continue
            snap = Snap.from_dict(data)
            snap.store_root = self.root
            snap.record_file = str(record)
            if snap.name:
                self.snaps[snap.name] = snap
                self._resync(snap)
        return self

    def _resync(self, snap):
        """Put a record's version back in line with the project it describes."""
        if not snap.directory or not Path(snap.directory).is_dir():
            return False
        from . import adopt        # here, not above: adopt imports this module
        live = adopt.packaged_version(snap.directory)
        if not live or live == snap.version:
            return False
        was, snap.version = snap.version, live
        snap.updated = now()
        self.resynced.append((snap.name, was, live))
        self._write(snap)
        return True

    def migrate(self):
        """Split the old single-file register into this one, once."""
        legacy = self.root / "snapkit.json"
        if not legacy.is_file() or (self.root / "snaps").is_dir():
            return False
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DatabaseError(f"{legacy} is not valid JSON: {exc}") from exc

        for name, record in (raw.get("snaps") or {}).items():
            snap = Snap.from_dict(record)
            snap.name = snap.name or name
            snap.store_root = self.root
            self._write(snap)
        legacy.rename(legacy.with_suffix(".json.migrated"))
        return True

    # -- writing -------------------------------------------------------------

    def _write(self, snap):
        """One snap: its record, and its recipe if there is one."""
        if snap.recipe_text:
            _atomic_write(snap.recipe_path, snap.recipe_text)
        elif snap.recipe_text == "":
            snap.recipe_path.unlink(missing_ok=True)

        # Clear the old file too, or one snap ends up with two of them.
        canonical = self.record_path(snap.name)
        if snap.record_file and Path(snap.record_file) != canonical:
            Path(snap.record_file).unlink(missing_ok=True)
        snap.record_file = str(canonical)
        _atomic_write(canonical,
                      json.dumps(snap.to_dict(), indent=2, ensure_ascii=False) + "\n")

    # -- the snaps in it -----------------------------------------------------

    def add(self, snap, replace=False):
        """Register a snap, or update the record of one already registered."""
        if not snap.name:
            raise DatabaseError("a snap needs a name")
        existing = self.snaps.get(snap.name)
        if existing and not replace and existing.repo and snap.repo \
                and existing.repo.lower() != snap.repo.lower():
            raise NameTaken(snap.name, existing.repo, snap.repo)
        snap.store_root = self.root
        snap.created = existing.created if existing else (snap.created or now())
        snap.updated = now()
        if not snap.directory:
            # Relative to this register, not to whatever SNAPKIT_HOME says.
            snap.directory = str(self.root / "projects" / snap.name)
        self.snaps[snap.name] = snap
        self._write(snap)
        return snap

    def free_name(self, wanted):
        """`wanted`, or the first name like it that nothing else holds."""
        if wanted not in self.snaps:
            return wanted
        for suffix in range(2, 100):
            candidate = f"{wanted}-{suffix}"
            if candidate not in self.snaps:
                return candidate
        raise DatabaseError(f"no free name like {wanted}")

    def get(self, name):
        snap = self.snaps.get(name)
        if snap is None:
            raise KeyError(f"no snap called {name} is registered")
        return snap

    def search(self, text):
        """Registered snaps matching a piece of text, best match first."""
        needle = (text or "").strip().lower()
        if not needle:
            return []
        # A pasted URL is a repository, and should find the snap made from it.
        try:
            from .github import parse_repo
            needle = parse_repo(needle).lower()
        except (ValueError, ImportError):
            pass

        scored = []
        for snap in self.snaps.values():
            name, repo = snap.name.lower(), snap.repo.lower()
            if repo == needle or name == needle:
                rank = 0
            elif name.startswith(needle):
                rank = 1
            elif needle in repo:
                rank = 2
            elif needle in name:
                rank = 3
            elif len(needle) >= 3 and needle in (snap.summary or "").lower():
                # Summaries are prose: "b" is in "extensibility".
                rank = 4
            else:
                continue
            scored.append((rank, snap.name, snap))
        return [snap for _, _, snap in sorted(scored, key=lambda row: row[:2])]

    def find_repo(self, repo):
        """The snap already made from this repository, if there is one."""
        for snap in self.snaps.values():
            if snap.repo.lower() == (repo or "").lower():
                return snap
        return None

    def remove(self, name):
        """Forget a snap entirely -- the record, the recipe, and the icon."""
        snap = self.get(name)
        kept = snap.kept_icon
        del self.snaps[name]
        self.record_path(name).unlink(missing_ok=True)
        snap.recipe_path.unlink(missing_ok=True)
        if kept:
            kept.unlink(missing_ok=True)
        return snap

    def all(self):
        return [self.snaps[name] for name in sorted(self.snaps)]

    def names(self):
        return sorted(self.snaps)

    def __contains__(self, name):
        return name in self.snaps

    def __len__(self):
        return len(self.snaps)

    def __iter__(self):
        return iter(self.all())


def _atomic_write(path, text):
    """Write a file through a temporary one in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".snapkit-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class DatabaseError(Exception):
    """The register could not be read or written."""


class NameTaken(DatabaseError):
    """A different repository is already registered under this name."""

    def __init__(self, name, held_by, wanted_by):
        self.name, self.held_by, self.wanted_by = name, held_by, wanted_by
        super().__init__(
            f"{name} is already registered, from {held_by} -- "
            f"{wanted_by} would replace it. Pass a different name to keep both.")
