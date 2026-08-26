"""From a repository or a file to a project, and from that to a .snap."""

import shutil
import contextlib
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import build as buildlib
from . import classify, github, inspect, local, net, recipe
from .db import Snap, now


class ForgeError(Exception):
    """Something the tool cannot go on without."""




@dataclass
class Plan:
    """What making this snap is going to involve."""

    origin: object                # Release | File
    candidates: list              # [classify.Candidate], best first
    chosen: object                # the Candidate being used
    name: str

    @property
    def title(self):
        return self.origin.title

    @property
    def rejected(self):
        return self.origin.rejected


@dataclass
class Release:
    """A payload attached to a GitHub release."""

    repo: str
    info: object                  # github.Repository
    release: object               # github.Release

    @property
    def title(self):
        return f"{self.repo} {self.release.tag}"

    @property
    def url(self):
        return self.info.url

    @property
    def description(self):
        return self.info.description

    @property
    def license(self):
        return self.info.license

    @property
    def version(self):
        return self.release.version

    @property
    def rejected(self):
        return classify.rejected(self.release.assets)

    def obtain(self, chosen, scratch, reporter):
        """Fetch the asset, and say what arrived."""
        archive = scratch / chosen.name
        reporter.step(f"fetching {chosen.name}")
        sha = net.download(chosen.asset.url, archive,
                           on_progress=reporter.progress)
        reporter.detail(f"sha256 {sha}")
        return archive, sha

    def source_for(self, snap, chosen, archive, sha, reporter):
        """What the recipe fetches, and the checksum to hold it to."""
        return chosen.asset.url, sha

    def track(self, snap, chosen, version):
        """How this snap is kept in step: the release it came from, and a"""
        snap.tag = self.release.tag
        snap.asset_pattern = classify.asset_pattern(chosen.name,
                                                    self.release.version)


@dataclass
class File:
    """A payload that is already on the disk."""

    path: Path                    # the file, or the folder it was found in
    version: str

    url = ""
    description = ""
    license = ""
    rejected = ()

    @property
    def title(self):
        return str(self.path)

    @property
    def repo(self):
        return ""

    def obtain(self, chosen, scratch, reporter):
        """Nothing to fetch -- read what is there and check it is intact."""
        archive = Path(chosen.asset.path)
        reporter.step(f"reading {archive.name}")
        sha = net.sha256_file(archive)
        reporter.detail(f"sha256 {sha}")
        return archive, sha

    def source_for(self, snap, chosen, archive, sha, reporter):
        """Put the file beside the recipe, and name it there."""
        archive = Path(archive)
        target = snap.path / archive.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() != archive.resolve():
            shutil.copy2(archive, target)
            reporter.detail(f"copied {archive.name} into {snap.path}")
        return archive.name, ""

    def track(self, snap, chosen, version):
        """How this snap is kept in step: by watching the folder."""
        snap.style = "artifact"
        snap.asset_glob = local.glob_for(chosen.name, version)
        snap.upstream = {"kind": "local", "glob": snap.asset_glob}


def plan(repo_text, reporter, tag=None, name=None, asset=None):
    """Work out what would be built, without building it."""
    repo = github.parse_repo(repo_text)
    reporter.step(f"looking up {repo}")
    info = github.describe(repo)
    release = github.release(repo, tag)
    reporter.detail(f"newest release {release.tag} ({release.version}), "
                    f"{len(release.assets)} files attached")

    candidates = classify.classify(release.assets)
    if not candidates:
        raise ForgeError(_nothing_usable(repo, release))

    chosen = choose(candidates, asset)
    reporter.detail(f"chose {chosen.name} -- {chosen.why}")
    return Plan(origin=Release(repo=repo, info=info, release=release),
                candidates=candidates, chosen=chosen,
                name=name or recipe.snap_name(repo.split("/")[1]))


def plan_local(path, reporter, name=None, asset=None):
    """Work out what packaging a file already on disk would involve."""
    path = Path(path).expanduser()
    if path.is_dir():
        found = local.find(path)
        if not found:
            raise ForgeError(
                f"no package in {path} -- looked for a .deb, an AppImage, or "
                f"an archive, and found nothing this tool can package")
        reporter.step(f"looking in {path}")
        reporter.detail(f"{len(found)} package{'s' if len(found) > 1 else ''} here")
    else:
        if not path.is_file():
            raise ForgeError(f"no such file: {path}")
        one = local.describe(path)
        if one is None:
            raise ForgeError(f"{path.name} is not something this tool can "
                             f"package: {classify.rejection(path.name) or 'unknown shape'}")
        reporter.step(f"opening {path.name}")
        # A file named outright is packaged as asked, but not silently.
        doubt = classify.rejection(path.name)
        if doubt:
            reporter.warn(f"{path.name} looks like it is {doubt}, and this "
                          f"builds amd64 snaps -- packaging it anyway")
        found = [one]

    candidates = [classify.Candidate(
        asset=github.Asset(name=f.name, url="", path=str(f.path)),
        kind=f.kind, score=f.score, why=f.why) for f in found]
    chosen = choose(candidates, asset)
    picked = next(f for f in found if f.name == chosen.name)
    reporter.detail(f"chose {chosen.name} -- {chosen.why}")
    if not picked.version:
        reporter.warn(f"no version in {chosen.name}, and none inside it -- "
                      f"the snap will be versioned 0")

    suggested = name or recipe.snap_name(
        local.name_from(picked.path, picked.kind) or picked.path.parent.name)
    return Plan(origin=File(path=path, version=picked.version or "0"),
                candidates=candidates, chosen=chosen, name=suggested)


def choose(candidates, asset=None):
    """One candidate: the best, or the one named, or the one numbered."""
    if not asset:
        return candidates[0]
    for candidate in candidates:
        if candidate.name == asset:
            return candidate
    if str(asset).isdigit() and 1 <= int(asset) <= len(candidates):
        return candidates[int(asset) - 1]
    raise ForgeError(
        f"no candidate called {asset}. This release offers:\n" +
        "\n".join(f"  {n}. {c.name}" for n, c in enumerate(candidates, 1)))


def _nothing_usable(repo, release):
    """Why a release cannot be packaged, in terms of what it does publish."""
    if not release.assets:
        return (f"{repo} {release.tag} has no files attached to it -- only the "
                f"source archives GitHub adds to every release, which this "
                f"tool cannot build from")
    passed_over = classify.rejected(release.assets)
    return (f"{repo} {release.tag} publishes nothing this tool can package:\n"
            + "\n".join(f"  {a.name} -- {why}" for a, why in passed_over[:6])
            + (f"\n  ... and {len(passed_over) - 6} more"
               if len(passed_over) > 6 else ""))


def create(plan_, reporter, directory=None):
    """Carry a plan out: get the payload, open it, write the project."""
    origin, chosen = plan_.origin, plan_.chosen
    with tempfile.TemporaryDirectory(prefix="snapkit-") as scratch:
        archive, sha = origin.obtain(chosen, Path(scratch), reporter)

        payload = _open_payload(archive, plan_, reporter, Path(scratch) / "payload")
        snap = _record(plan_, payload, directory)
        snap.icon = _install_icon(snap, payload, reporter)
        source, checksum = origin.source_for(snap, chosen, archive, sha, reporter)
        snap.snapcraft_yaml = recipe.from_record(
            snap, payload, source, sha=checksum,
            description=origin.description, icon=snap.icon)

    write(snap, reporter)
    return snap


def _open_payload(archive, plan_, reporter, destination):
    """Unpack the asset and say what turned up, or why it is unusable."""
    reporter.step("opening the payload")
    try:
        payload = inspect.look(archive, plan_.chosen.kind, destination,
                               wanted=plan_.name)
    except inspect.InspectionError as exc:
        raise ForgeError(str(exc)) from exc

    if not payload.command:
        raise ForgeError(
            f"no program found inside {plan_.chosen.name} -- it may be a "
            f"library, or its binary may not be marked executable")

    reporter.detail(f"command {payload.command}")
    for label, value in (("desktop entry", payload.desktop), ("icon", payload.icon)):
        if value:
            reporter.detail(f"{label} {value}")
    if payload.traits:
        reporter.detail("looks like " + ", ".join(sorted(payload.traits)))
    if payload.libraries:
        # Not fatal: a strict snap takes its libraries from the base anyway.
        reporter.warn(f"{payload.command} wants libraries this host does not "
                      f"have: {' '.join(payload.libraries)}")
    return payload


def _record(plan_, payload, directory=None):
    """The database record for what was just resolved and opened."""
    origin, chosen = plan_.origin, plan_.chosen
    version = payload.version or origin.version
    snap = Snap(
        name=plan_.name, repo=origin.repo, url=origin.url, kind=chosen.kind,
        version=version, asset=chosen.name,
        summary=recipe.summarise(payload.summary or origin.description, plan_.name),
        description=origin.description, license=origin.license,
        command=payload.command, plugs=recipe.plugs_for(payload.traits),
        directory=str(directory) if directory else "", created=now())
    origin.track(snap, chosen, version)
    return snap


def _install_icon(snap, payload, reporter):
    """Copy the icon out of the payload and put it next to the recipe."""
    if not payload.icon:
        return ""
    source = payload.root / payload.icon
    suffix = source.suffix.lower()
    if suffix not in (".png", ".svg"):
        reporter.detail(f"leaving {payload.icon} out: snapcraft takes png or svg")
        return ""
    relative = f"snap/gui/{snap.name}{suffix}"
    target = snap.path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    # A copy beside the register, so the project survives its directory.
    snap.keep_icon(source)
    reporter.detail(f"icon copied to {relative}")
    return relative


def write(snap, reporter):
    """Put the project on disk: the recipe, and where it came from."""
    directory = snap.path
    if not snap.snapcraft_yaml:
        # Never hand one of these an empty recipe; the build goes wrong oddly.
        if not directory.is_dir():
            raise ForgeError(f"{snap.name} holds no recipe and {directory} is "
                             f"not there, so there is nothing to write")
        reporter.detail(f"{snap.name} keeps its own build; nothing to write")
        return directory

    (directory / "snap").mkdir(parents=True, exist_ok=True)
    yaml_path = directory / "snap" / "snapcraft.yaml"
    yaml_path.write_text(snap.snapcraft_yaml, encoding="utf-8")

    # Written once and left alone: a README is a thing people rewrite.
    readme = directory / "README.md"
    if not readme.exists():
        readme.write_text(_readme(snap), encoding="utf-8")

    # The icon path is relative, so it has to come back with the directory.
    if snap.icon and not (directory / snap.icon).is_file():
        kept = snap.kept_icon
        if kept:
            target = directory / snap.icon
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(kept, target)
            reporter.detail(f"restored {snap.icon}")
        else:
            reporter.warn(f"{snap.icon} is named by the recipe but no copy of "
                          f"it was kept; remove the icon: line or recreate")
    reporter.step(f"wrote {yaml_path}")
    return directory


def adopt(snap, reporter):
    """Take an edited snapcraft.yaml back into the record."""
    yaml_path = snap.path / "snap" / "snapcraft.yaml"
    if not yaml_path.is_file():
        return False
    text = yaml_path.read_text(encoding="utf-8")
    if text == snap.snapcraft_yaml:
        return False
    snap.snapcraft_yaml = text
    reporter.detail("took the edited snapcraft.yaml back into the register")
    return True


def package(snap, reporter, build_it=True):
    """Build a snap from its record, without going upstream for anything."""
    adopt(snap, reporter)
    write(snap, reporter)
    if not build_it:
        how = snap.build_with or (f"snapkit build {snap.name}" if snap.pack
                                  else "snapcraft")
        reporter.detail(f"build it with: cd {snap.path} && {how}")
        return None
    return build(snap, reporter)


def build(snap, reporter, extra=()):
    """Build the project, and find what it produced."""
    directory = snap.path
    if not directory.is_dir():
        raise ForgeError(f"no project at {directory} -- write it out first")
    before = {p.name for p in directory.glob("*.snap")}

    if snap.pack:
        # pack.py is imported, not run, so it needs nothing on the path.
        reporter.step(f"{snap.pack} ({directory})")
        # A front end taking the output does not need the terminal as well.
        holding = contextlib.nullcontext() if reporter.captures_output \
            else reporter.suspended()
        with holding:
            try:
                buildlib.run_pack(snap.name, directory, snap.pack, reporter)
            except buildlib.BuildError as exc:
                raise ForgeError(str(exc)) from exc
            except subprocess.CalledProcessError as exc:
                command = " ".join(str(c) for c in exc.cmd) \
                    if isinstance(exc.cmd, list) else exc.cmd
                raise ForgeError(f"{command} exited with status "
                                 f"{exc.returncode}") from exc
    else:
        if snap.build_with:
            command, shell = snap.build_with, True
            reporter.step(f"{snap.build_with} ({directory})")
        else:
            if not (directory / "snap" / "snapcraft.yaml").is_file():
                raise ForgeError(f"no snap/snapcraft.yaml at {directory} -- "
                                 f"write it out first")
            try:
                buildlib.snapcraft_preflight("--destructive-mode" in extra)
            except buildlib.BuildError as exc:
                raise ForgeError(str(exc)) from exc
            command, shell = ["snapcraft", "pack", *extra], False
            reporter.step(f"snapcraft pack ({directory})")

        def run_it():
            if reporter.captures_output:
                return buildlib.stream(command, reporter, cwd=directory, shell=shell)
            with reporter.suspended():
                return subprocess.run(command, cwd=directory, shell=shell)

        done = run_it()
        if done.returncode != 0 and not shell:
            # A wedged container kills every later build before it starts.
            stale = buildlib.stale_instance()
            if stale and buildlib.drop_instance(stale):
                reporter.warn(f"removed the wedged build container {stale}, "
                              f"and building again")
                done = run_it()
        if done.returncode != 0:
            raise ForgeError(f"the build exited with status {done.returncode}")

    made = sorted(p for p in directory.glob("*.snap") if p.name not in before)
    built = made[-1] if made else None
    if built is None:
        # A rebuild of the same version overwrites rather than adds.
        existing = sorted(directory.glob(f"{snap.name}_*.snap"),
                          key=lambda p: p.stat().st_mtime)
        built = existing[-1] if existing else None
    if built is None:
        raise ForgeError("snapcraft finished but produced no .snap")
    reporter.result(f"built {built.name} "
                    f"({built.stat().st_size / 1e6:.0f} MB)")
    snap.record_build(snap.version)
    return built
























def _readme(snap):
    return f"""# {snap.name}

{snap.summary}

Packaged from [{snap.repo}]({snap.url}) by snapkit, from the release asset
`{snap.asset}`. This snap is not published or endorsed by the upstream
project.

## Building

    cd {snap.path}
    snapcraft

## Installing what you built

    sudo snap install --dangerous {snap.name}_{snap.version}_amd64.snap

## Updating

`snapkit` checks {snap.repo} for a newer release and rewrites
`snap/snapcraft.yaml` for you. Anything you change in that file is kept:
an update only moves the version, the source URL and its checksum.
"""
