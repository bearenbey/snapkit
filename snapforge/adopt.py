"""Taking a snap project that already exists into the register.

Someone maintaining snaps by hand has directories full of them, each with a
recipe, a version and an upstream it came from. Registering those is worth
more than starting empty: they can be listed, searched, built and -- where
there is a GitHub release behind them -- checked and updated, without being
rewritten first.

Nothing is inferred that cannot be read. A project whose upstream cannot be
worked out is registered without one and says so, rather than being given a
guess that would send an update at the wrong repository.
"""

import re
from pathlib import Path

from . import classify
from .db import Snap, now

# Where a recipe lives; the second is metadata for a hand-assembled tree.
SNAPCRAFT_YAML = "snap/snapcraft.yaml"
META_YAML = "overlay/meta/snap.yaml"

ICON_DIRS = ("snap/gui", "overlay/meta/gui", "meta/gui")
ICON_SUFFIXES = (".png", ".svg")

# Files a project keeps its upstream payload in, by what they are.
class NotAProject(Exception):
    """There is no snap project in that directory."""


def find_recipe(directory):
    """(path, is_snapcraft) for whichever recipe the project has."""
    snapcraft = directory / SNAPCRAFT_YAML
    if snapcraft.is_file():
        return snapcraft, True
    meta = directory / META_YAML
    if meta.is_file():
        return meta, False
    raise NotAProject(f"{directory.name} has neither {SNAPCRAFT_YAML} nor "
                      f"{META_YAML}")


def yaml_field(text, field):
    """One top-level scalar out of a yaml file, without a yaml parser.

    These are read to describe a project, not to build it, and the fields
    wanted are all plain one-line scalars at the top level.
    """
    found = re.search(rf"(?m)^{re.escape(field)}:\s*(.*)$", text)
    if not found:
        return ""
    return found.group(1).strip().strip("'\"")


def yaml_block(text, field):
    """A `field: |` block, dedented."""
    found = re.search(rf"(?ms)^{re.escape(field)}:\s*\|\s*\n(.*?)(?=^\S|\Z)", text)
    if not found:
        return yaml_field(text, field)
    lines = found.group(1).splitlines()
    pad = min((len(l) - len(l.lstrip()) for l in lines if l.strip()), default=0)
    return "\n".join(l[pad:] for l in lines).strip()


def find_repo(directory, text):
    """The GitHub repository this project packages, if it says anywhere.

    The recipe first, then the README. A URL pointing at a release, an issue
    or a file still names the repository, so those are trimmed back to it.
    """
    sources = [text]
    readme = directory / "README.md"
    if readme.is_file():
        sources.append(readme.read_text(encoding="utf-8", errors="replace"))

    counted = {}
    for source in sources:
        for owner, name in re.findall(
                r"github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)", source):
            # rstrip takes characters: "irssi/irssi".rstrip(".git") loses an i.
            name = name.removesuffix(".git")
            if not name or name in ("releases", "issues", "blob", "tree", "raw"):
                continue
            repo = f"{owner}/{name}"
            counted[repo] = counted.get(repo, 0) + 1
    if not counted:
        return ""
    return max(counted, key=counted.get)


def find_artifact(directory, text):
    """The upstream file this project builds from, and what kind it is.

    Either a file sitting in the directory, or the one a `source:` line names.
    A project that builds from a source tarball has neither of the kinds this
    tool knows how to package, and says so by answering with no kind.
    """
    source = yaml_field(text, "source") or ""
    found = re.search(r"(?m)^\s*source:\s*(\S+)", text)
    if found:
        source = found.group(1)
    name = source.rsplit("/", 1)[-1].lstrip("./") if source else ""

    if not name or classify.kind_of(name) == "":
        # Best first, by the classifier's score: a second list only drifts.
        here = sorted(classify.packages(directory),
                      key=lambda p: (-classify.score(p.name)[0], p.name))
        if here:
            name = here[0].name
    return name, classify.kind_of(name) if name else ""


def version_from(source, artifact):
    """The version a project is on, when its recipe does not carry one.

    A recipe that lets snapcraft adopt the version from the source has no
    `version:` field, and the only statement of which release it is on is the
    URL it fetches -- the tag in the path, or the version in the file name.
    """
    for text in (source or "", artifact or ""):
        found = re.search(r"/(?:download|tags)/v?([0-9][^/]*?)/", text)
        if found:
            return found.group(1).removesuffix("-stable")
        # A tag can end at the file name: .../tags/v0.41.0.tar.gz
        found = re.search(r"[/\-_]v?([0-9]+(?:\.[0-9]+)+)", text)
        if found:
            return found.group(1)
        found = re.search(r"[-_]([0-9]{3,})[-_.]", text)
        if found:                       # a bare build number, as sublime-text has
            return found.group(1)
    return ""


def source_in(text):
    """The first `source:` in a recipe, which is where an adopted version hides."""
    found = re.search(r"(?m)^\s*source:\s*(\S+)", text)
    return found.group(1) if found else ""


def packaged_version(directory):
    """The version the project on disk is on now, or "" if it cannot be read.

    A record stores the version its project was on when it was imported, but
    a project is a directory somebody can edit, and its recipe is meant to be
    edited. So the stored version is a cache, this is the authority, and
    db.Database.load() puts one back in line with the other rather than
    letting them drift apart.
    """
    directory = Path(directory)
    try:
        recipe, _ = find_recipe(directory)
        text = recipe.read_text(encoding="utf-8", errors="replace")
    except (NotAProject, OSError):
        return ""
    artifact, _kind = find_artifact(directory, text)
    return yaml_field(text, "version") or version_from(source_in(text), artifact)


def find_icon(directory):
    """The icon the project ships, wherever it keeps it."""
    for relative in ICON_DIRS:
        folder = directory / relative
        if not folder.is_dir():
            continue
        icons = sorted(p for p in folder.iterdir()
                       if p.suffix.lower() in ICON_SUFFIXES)
        if icons:
            return icons[0]
    return None


def read(directory, repo=None):
    """Everything that can be read off an existing project, as a record."""
    directory = Path(directory).resolve()
    recipe, is_snapcraft = find_recipe(directory)
    text = recipe.read_text(encoding="utf-8", errors="replace")

    name = yaml_field(text, "name") or directory.name.removesuffix("-snap")
    artifact, kind = find_artifact(directory, text)
    source = source_in(text)
    version = yaml_field(text, "version") or version_from(source, artifact)

    # An inferred repository is recorded but left inert.
    confirmed = bool(repo)
    resolved = repo or find_repo(directory, text)

    snap = Snap(
        name=name,
        repo=resolved,
        url=f"https://github.com/{resolved}" if resolved else "",
        kind=kind,
        version=version,
        asset=artifact,
        asset_pattern=(classify.asset_pattern(artifact, version)
                       if confirmed and artifact and version and kind else ""),
        summary=yaml_field(text, "summary"),
        description=yaml_block(text, "description")[:400],
        license=yaml_field(text, "license"),
        confinement=yaml_field(text, "confinement") or "strict",
        grade=yaml_field(text, "grade") or "stable",
        base=yaml_field(text, "base") or "core24",
        command=_first_command(text),
        # pack.py takes a Build; build.py is older and run as a program.
        pack="pack.py" if (directory / "pack.py").is_file() else "",
        build_with=("./build.py" if (directory / "build.py").is_file()
                    and not (directory / "pack.py").is_file() else ""),
        directory=str(directory),
        recipe_text=text if is_snapcraft else "",
        created=now(),
    )
    return snap, recipe, is_snapcraft, confirmed


def _first_command(text):
    found = re.search(r"(?m)^\s{4}command:\s*(\S+)", text)
    return found.group(1) if found else ""


def reasons(snap, is_snapcraft, confirmed=False):
    """What is and is not true of an imported record, in words."""
    notes = []
    if snap.upstream.get("kind") == "local":
        notes.append(f"tracked against its own folder: a newer "
                     f"{snap.upstream.get('glob')} put there reads as an update")
    elif snap.upstream:
        where = snap.upstream.get("package") or snap.upstream.get("url") or ""
        notes.append(f"tracked against {snap.upstream.get('kind')}"
                     + (f" ({where})" if where else ""))
    elif not snap.repo:
        notes.append("no upstream repository found -- it will not be checked "
                     "for new releases")
    elif not confirmed:
        notes.append(f"looks like {snap.repo}, read off its own files -- pass "
                     f"--repo to confirm that and have it checked")
    elif not snap.asset_pattern:
        notes.append(f"upstream is {snap.repo}, but no release file could be "
                     f"matched, so it will not be checked")
    if not is_snapcraft:
        notes.append("assembles its own tree, so its recipe is not a "
                     "snapcraft.yaml")
    if snap.pack:
        notes.append(f"assembles its own tree with {snap.pack}")
    elif snap.build_with:
        notes.append(f"builds with {snap.build_with}")
    return notes
