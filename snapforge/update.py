"""Keeping a snap in step with what it was made from.

The other half of the tool. `project.py` turns something -- a release, a file
-- into a snap; this asks whether that something has moved since, and moves
the packaging onto it when it has.

There are two things a project can mean by "the source", and an update has to
know which. Most recipes name a URL and leave the fetching to snapcraft, so
an update repoints that line and rewrites its checksum. The rest open a file
that has to be sitting in the project directory before the build can start,
so an update puts it there, drops the superseded one, and replaces the
version wherever the project spells it out. That is `Snap.style`.

What is asked, and of whom, is on the record too: a repository by default, or
one of the shapes in `sources.py` -- an apt index, a directory listing, a
download endpoint that redirects, a tag archive, or the project's own folder.
"""

import shutil
import tempfile
from pathlib import Path

from dataclasses import dataclass

from . import classify, github, net, recipe, rewrite, sources
from .db import now
from .net import NetworkError
from .project import ForgeError, write


class NotTracked(ForgeError):
    """This snap has no upstream repository to ask about.

    Not every snap comes from a GitHub release. One imported from a project
    that fetches its source some other way is still worth having in the
    register -- it can still be built and listed -- but there is nothing to
    check it against, and saying so is different from failing.
    """


# What a check can conclude, in one copy: the two front ends had drifted.
STATES = {
    "current": "up to date",
    "behind": "UPDATE AVAILABLE",
    "untracked": "not tracked upstream",
    "error": "upstream unreachable",
}


@dataclass
class Situation:
    """What checking one snap found, including when the check could not run.

    `check` raises for the two cases that are not about the version -- an
    upstream nobody recorded, and one that could not be reached -- because a
    caller that only wants to update needs them to interrupt. A caller that
    wants to *report* needs them as answers instead, and there are three of
    those. This is that shape, so the decision is made once.
    """

    state: str
    release: object = None
    asset: object = None
    note: str = ""
    problem: str = ""

    @property
    def words(self):
        return STATES[self.state]

    @property
    def behind(self):
        return self.state == "behind"

    @property
    def latest(self):
        return self.release.version if self.release is not None else ""


def situation(snap, force=False):
    """`check`, with its two exceptions turned into answers."""
    try:
        release, asset, note = check(snap, force)
    except NotTracked as exc:
        return Situation("untracked", problem=str(exc))
    except (NetworkError, ForgeError) as exc:
        return Situation("error", problem=str(exc))
    return Situation("behind" if asset is not None else "current",
                     release=release, asset=asset, note=note)


def missing_artifact(snap):
    """True when the file this project's build opens is not there.

    A project whose artifact has gone -- never fetched, or cleared out to save
    the disk -- is behind whatever its recipe says, however current that is.
    Reported as an update rather than as "up to date, and broken", because
    fetching the file back is exactly what an update does.
    """
    if snap.style != "artifact" or not snap.asset_glob:
        return False
    directory = snap.path
    return directory.is_dir() and not any(directory.glob(snap.asset_glob))


def check(snap, force=False):
    """What upstream has now, against what this snap was built from.

    Returns (release, asset, note): a newer release and the file to build it
    from, or (release, None, "") when there is nothing to do. `note` is set
    when the stored pattern stopped matching and something else was taken
    instead, because that is a thing to say out loud rather than to do
    quietly.

    `force` asks for the file of the current release rather than of a newer
    one, which is how a project that is already up to date is redone.

    Three steps, in this order and not another: ask what there is, decide
    whether it is worth doing anything about, and only then work out which
    file. Choosing the file can cost a second request -- some projects
    publish their checksums in a manifest beside the release -- and a check
    that is going to answer "up to date" should not be making it.
    """
    release = _resolve(snap)
    if _settled(snap, release, force):
        return release, None, ""
    asset, note = _asset_for(snap, release)
    return release, asset, note


def _resolve(snap):
    """What the thing this snap was made from offers now.

    A GitHub release unless the record says otherwise; `upstream` names one
    of the other shapes -- an apt index, a directory listing, a download
    endpoint that redirects, a tag archive, or the project's own folder.
    """
    if snap.upstream:
        return sources.resolve(snap.upstream, directory=snap.path)
    if not snap.repo:
        raise NotTracked(f"{snap.name} has no upstream repository recorded, so "
                         f"there is nothing to check it against")
    if not snap.asset_pattern:
        raise NotTracked(f"{snap.name} has no release file recorded to match "
                         f"against, so there is nothing to check")
    return github.release(snap.repo)


def _settled(snap, release, force):
    """Whether there is nothing to do, for either kind of upstream."""
    if force:
        return False
    if missing_artifact(snap):
        # Current, but the file its build opens is gone, so fetch it back.
        return False
    if release.version != snap.version:
        return False
    # Only when both have one: an imported project has a version and no tag.
    return not (snap.tag and release.tag and release.tag != snap.tag)


def _asset_for(snap, release):
    """The file to build the new version from, and anything to say about it."""
    if snap.upstream:
        # Off the record, not the upstream config: discord names no glob.
        return github.Asset(
            name=release.asset, url=release.url, sha=release.sha,
            glob=snap.asset_glob or release.glob, path=release.path,
            local=snap.local_asset or (
                release.local if release.local != release.asset else "")), ""

    asset = classify.match_pattern(release.assets, snap.asset_pattern)
    if asset is not None:
        return _dressed(snap, release, asset), ""

    # The pattern stopped matching; upstreams rename assets between releases.
    same_kind = [c for c in classify.classify(release.assets) if c.kind == snap.kind]
    if not same_kind:
        raise ForgeError(
            f"{snap.name}: {release.tag} publishes nothing of the same kind "
            f"({snap.kind}) as {snap.asset} -- recreate the snap to choose again")
    best = same_kind[0]
    return _dressed(snap, release, best.asset), (
        f"upstream no longer publishes {snap.asset}; "
        f"{best.name} is the closest thing in {release.tag}")


def _dressed(snap, release, asset):
    """A release asset, told what this particular project does with it.

    What the build opens the file as, what matches every version of it, and
    where upstream publishes its checksum are properties of the project
    rather than of the release, so they are on the record and are put onto
    the asset here -- at the one place an update takes hold of it.
    """
    if not (snap.checksums or snap.local_asset or snap.asset_glob):
        return asset
    sha = ""
    if snap.checksums:
        base = f"https://github.com/{snap.repo}/releases/download/{release.tag}"
        sha = sources.manifest_sha(
            snap.checksums["url"].format(base=base, tag=release.tag,
                                         version=release.version, asset=asset.name),
            asset.name, required=snap.checksums.get("required", True))
    return github.Asset(name=asset.name, url=asset.url, local=snap.local_asset,
                        sha=sha, glob=snap.asset_glob)


def update(snap, release, asset, reporter):
    """Move a registered snap onto a newer release.

    Two things a project can mean by "the source", and an update has to know
    which: a recipe that names a URL snapcraft will fetch at build time, or a
    file that has to be sitting in the project directory before its build can
    open it. See `Snap.style`.
    """
    was = snap.version
    if snap.style == "artifact":
        _update_artifact(snap, release, asset, reporter, was)
    else:
        _update_recipe(snap, release, asset, reporter)

    snap.version, snap.asset = release.version, asset.name
    if release.tag:
        snap.tag = release.tag
    # Relearn from the name that worked, so a rename is noticed once.
    if not snap.upstream:
        snap.asset_pattern = classify.asset_pattern(asset.name, release.version)
    snap.updated = now()
    write(snap, reporter)
    reporter.result(f"{snap.name} is now at {release.version}")
    return snap


def _fetch(url, target, sha, reporter):
    reporter.step(f"fetching {url.rsplit('/', 1)[-1]}")
    got = net.download(url, target, sha, on_progress=reporter.progress)
    if sha:
        reporter.detail("sha256 verified against upstream")
    else:
        reporter.detail(f"sha256 {got} (upstream publishes none)")
    return got


def _update_recipe(snap, release, asset, reporter):
    """Repoint the recipe at the new source, and rewrite its checksum.

    Where upstream publishes a checksum it goes in as given and snapcraft
    checks the source against it at build time; where it does not, the file
    is fetched here purely to compute one, and thrown away again -- snapcraft
    will fetch it properly at build time.
    """
    sha = asset.sha
    with tempfile.TemporaryDirectory(prefix="snapkit-") as scratch:
        if not sha or snap.verify:
            target = Path(scratch) / asset.name
            sha = _fetch(asset.url, target, asset.sha, reporter) or sha
            _verified(snap, target, release, reporter)

    if snap.source_anchor:
        # Anchored: a recipe can name more than one source.
        yaml_path = snap.path / "snap" / "snapcraft.yaml"
        changes = rewrite.repoint_yaml(
            yaml_path, snap.source_anchor, asset.url, sha,
            release.version if snap.write_version else "")
        _say_changes(changes, reporter)
        if yaml_path.is_file():
            snap.snapcraft_yaml = yaml_path.read_text(encoding="utf-8")
        return

    old_url = _source_url(snap.snapcraft_yaml)
    snap.snapcraft_yaml = recipe.repoint(
        snap.snapcraft_yaml, snap.version, release.version, old_url, asset.url, sha)


def _update_artifact(snap, release, asset, reporter, was):
    """Fetch the file the build opens, then rewrite the version around it.

    Unlike a recipe project the download lands in the project directory
    rather than in a scratch directory, so a check that rejects it has to
    take it back out again: what a failed check must not leave behind is a
    tarball the next build would happily open.

    The version is then replaced wherever the project spells it out -- the
    recipe, `overlay/meta/snap.yaml`, the README -- because for these
    projects the packaging is the record of which release it is on, and half
    a bump is worse than none.
    """
    directory = snap.path
    if not directory.is_dir():
        raise ForgeError(f"no project at {directory} -- {snap.name} builds from "
                         f"a file in its own directory, so there has to be one")

    superseded = sorted(p for p in directory.glob(asset.glob)
                        if p.name != asset.filename) if asset.glob else []
    fetched = directory / asset.filename

    if asset.path and Path(asset.path).resolve() == fetched.resolve():
        # Somebody put it there: nothing to fetch, nothing to check it against.
        reporter.step(f"packaging {fetched.name}, which is already here")
    else:
        if asset.path:
            reporter.step(f"copying in {Path(asset.path).name}")
            shutil.copy2(asset.path, fetched)
        else:
            _fetch(asset.url, fetched, asset.sha, reporter)
        try:
            _verified(snap, fetched, release, reporter)
        except Exception:
            fetched.unlink(missing_ok=True)
            raise

    changes = rewrite.rewrite_versions(directory, was, release.version,
                                       snap.asset, asset.filename)
    _say_changes(changes, reporter)

    for old in superseded:
        old.unlink()
        reporter.detail(f"removed superseded {old.name}")

    # The register's copy has to follow, or `package` puts the old one back.
    yaml_path = directory / "snap" / "snapcraft.yaml"
    if yaml_path.is_file():
        snap.snapcraft_yaml = yaml_path.read_text(encoding="utf-8")


def _verified(snap, path, release, reporter):
    """Run the record's check over a download, and say what it found."""
    if not snap.verify:
        return ""
    found = sources.verify(snap.verify, path, release)
    if found:
        reporter.detail(found)
    return found


def _say_changes(changes, reporter):
    """Every line a rewrite touched, so a bump stays reviewable without
    diffing afterwards -- which matters most when a version string turns up
    somewhere it was not expected."""
    for change in changes:
        reporter.detail(change.path)
        for number, text in change.lines:
            reporter.detail(f"  {number:>4}  {text.strip()}")


def _source_url(yaml_text):
    for line in yaml_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("source:") and "http" in stripped:
            return stripped.split(":", 1)[1].strip()
    return ""
