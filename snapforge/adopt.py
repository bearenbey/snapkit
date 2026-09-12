"""Taking a snap project that already exists into the register."""

import re
from pathlib import Path

from . import classify, recipe, sources, versions
from .db import Snap, now
from .recipe import META_YAML, SNAPCRAFT_YAML

ICON_DIRS = ("snap/gui", "overlay/meta/gui", "meta/gui")
ICON_SUFFIXES = (".png", ".svg")


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


def find_repo(directory, text):
    """The GitHub repository this project packages, if it says anywhere."""
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
    """The upstream file this project builds from, and what kind it is."""
    source = source_in(text)
    name = source.rsplit("/", 1)[-1] if source else ""

    if not name or classify.kind_of(name) == "":
        # Best first, by the classifier's score: a second list only drifts.
        here = sorted(classify.packages(directory),
                      key=lambda p: (-classify.score(p.name)[0], p.name))
        if here:
            name = here[0].name
    return name, classify.kind_of(name) if name else ""


def source_in(text):
    """The first part's `source:`, where an adopted version hides."""
    named = recipe.sources(text)
    return named[0][1] if named else ""


def packaged_version(directory):
    """The version the project on disk is on now, or "" if it cannot be read."""
    directory = Path(directory)
    try:
        found, _ = find_recipe(directory)
        text = found.read_text(encoding="utf-8", errors="replace")
    except (NotAProject, OSError):
        return ""
    artifact, _kind = find_artifact(directory, text)
    return (recipe.field(text, "version")
            or versions.from_name(source_in(text), artifact))


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


def take_icon(snap, directory, store=None):
    """Record the icon a project ships, and keep a copy beside the register."""
    icon = find_icon(Path(directory))
    if not icon:
        return ""
    # Named first, or the copy lands in the default home and is lost.
    if store is not None:
        snap.store_root = Path(store)
    # Where it is, so a later write restores it there and nowhere else.
    snap.icon = icon.relative_to(directory).as_posix()
    snap.keep_icon(icon)
    return snap.icon


def read(directory, repo=None):
    """Everything that can be read off an existing project, as a record."""
    directory = Path(directory).resolve()
    found, is_snapcraft = find_recipe(directory)
    text = found.read_text(encoding="utf-8", errors="replace")

    name = recipe.field(text, "name") or directory.name.removesuffix("-snap")
    artifact, kind = find_artifact(directory, text)
    source = source_in(text)
    version = recipe.field(text, "version") or versions.from_name(source, artifact)
    has_pack = (directory / "pack.py").is_file()

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
        summary=recipe.field(text, "summary"),
        description=recipe.block(text, "description")[:400],
        license=recipe.field(text, "license"),
        confinement=recipe.field(text, "confinement") or "strict",
        grade=recipe.field(text, "grade") or "stable",
        base=recipe.field(text, "base") or recipe.BASE,
        command=recipe.first_command(text),
        # pack.py takes a Build; build.py is older and run as a program.
        pack="pack.py" if has_pack else "",
        build_with=("./build.py" if (directory / "build.py").is_file()
                    and not has_pack else ""),
        directory=str(directory),
        recipe_text=text if is_snapcraft else "",
        created=now(),
    )
    return snap, found, is_snapcraft, confirmed


def reasons(snap, is_snapcraft, confirmed=False):
    """What is and is not true of an imported record, in words."""
    notes = []
    if snap.upstream.get("kind") == "local":
        notes.append(f"tracked against its own folder: a newer "
                     f"{snap.upstream.get('glob')} put there reads as an update")
    elif snap.upstream:
        notes.append(f"tracked against {sources.label(snap)}")
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
