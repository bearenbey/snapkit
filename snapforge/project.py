"""From a repository or a file to a project on disk, and from that to a .snap.

This is the sequence the tool exists to run:

    resolve   what does this repository publish, and what is the newest of it
              -- or, for a file that was handed over, what is that file
    choose    which of those is the one to build from
    fetch     get it, and check what arrived; a file already here is skipped
    inspect   open it and find out where everything is
    write     a snapcraft.yaml around what was found, and a project around that
    register  put the record in the database
    build     hand it to snapcraft, or to the project's own pack.py

Keeping a snap in step with what it was made from afterwards is the other
half, and lives in `update.py`.

Each step reports through a Reporter, so the same code runs under the
dashboard and under a plain terminal without knowing which it is in.
"""

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
    """What making this snap is going to involve.

    Made before anything is written, so that the dashboard can show it and a
    person can change their mind about the asset or the name while it is
    still free to do so.

    The `origin` is where the payload comes from, and is the only thing that
    differs between making a snap out of a release and making one out of a
    file somebody already has. Everything downstream -- fetching, opening,
    recording, writing the recipe -- asks the origin rather than asking which
    kind of plan this is, because a boolean tested in four places is how the
    two paths quietly stop agreeing.
    """

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
        """What the recipe fetches, and the checksum to hold it to.

        The URL, because snapcraft can fetch it again at build time -- and
        upstream published it, so the checksum means something.
        """
        return chosen.asset.url, sha

    def track(self, snap, chosen, version):
        """How this snap is kept in step: the release it came from, and a
        pattern that will still match the asset in the next one."""
        snap.tag = self.release.tag
        snap.asset_pattern = classify.asset_pattern(chosen.name,
                                                    self.release.version)


@dataclass
class File:
    """A payload that is already on the disk.

    Nothing is known about where it came from, and none of it is guessed:
    the description, the licence and the repository are simply empty, which
    is true and leaves the recipe with less to say rather than with
    something invented.
    """

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
        """Put the file beside the recipe, and name it there.

        By name rather than by path, so a project that carries its own
        payload is still a directory you can move somewhere else and build.

        No checksum goes with it. `source-checksum` says "this is what
        upstream published"; for a file somebody put in a folder there is no
        upstream to have published anything, and a checksum of the file
        against itself would only restate that it had not changed.
        """
        archive = Path(archive)
        target = snap.path / archive.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() != archive.resolve():
            shutil.copy2(archive, target)
            reporter.detail(f"copied {archive.name} into {snap.path}")
        return archive.name, ""

    def track(self, snap, chosen, version):
        """How this snap is kept in step: by watching the folder.

        The payload lives in the project rather than being fetched at build
        time, so an update is about that directory -- a newer file dropped in
        beside it is the only thing there is to notice, and the only thing
        this tool can honestly claim to see.
        """
        snap.style = "artifact"
        snap.asset_glob = local.glob_for(chosen.name, version)
        snap.upstream = {"kind": "local", "glob": snap.asset_glob}


def plan(repo_text, reporter, tag=None, name=None, asset=None):
    """Work out what would be built, without building it.

    `asset` picks one of the candidates by name, or by its position in the
    ranking as it is printed. Without it the best-scoring one is taken --
    which is a default, not a verdict: the whole ranking comes back on the
    Plan so a caller can offer the rest.
    """
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
    """Work out what packaging a file already on disk would involve.

    `path` is the file itself, or a directory to look in -- which is the case
    worth having, because the question people actually have is "I downloaded
    this, can you make a snap of it" and the answer should not require them
    to spell the filename out.
    """
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
    """Carry a plan out: get the payload, open it, write the project.

    All of it happens inside one temporary directory, and the record is
    finished before that directory goes away -- the icon has to be copied out
    and the recipe has to be written while there is still a payload to write
    them from.
    """
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
    """Copy the icon out of the payload and put it next to the recipe.

    snapcraft resolves a top-level `icon:` against the project directory, not
    against anything the build produces, so an icon that exists only inside
    the downloaded payload names a file that will never be there. It also
    takes png and svg and nothing else, which rules out the .xpm and the
    gzipped .svgz some projects still ship.
    """
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
    """Put the project on disk: the recipe, and a note saying where it came from.

    The recipe comes out of the record rather than being regenerated, so what
    lands here is exactly what the database holds -- edit the file and re-add
    it, or delete the directory and write it out again, and the two agree.
    """
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
    """Take an edited snapcraft.yaml back into the record.

    The file on disk wins: someone changed it for a reason, and the database
    is meant to hold what was built, not what was once generated.
    """
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
    """Build a snap from its record, without going upstream for anything.

    The register holds the recipe and a copy of the icon, so a snap that has
    been made once can be made again from what is written down -- no
    repository, no release lookup, no download of the payload by this tool.
    snapcraft still fetches the source the recipe names, and checks it against
    the checksum that was written in at the time.

    That is what makes a registered snap worth having: the second time is a
    name, not a URL.

    Whatever is on disk wins over the copy in the register: someone may have
    edited the recipe since, and writing the stored one over it would undo
    that without saying so.
    """
    adopt(snap, reporter)
    write(snap, reporter)
    if not build_it:
        how = snap.build_with or (f"snapkit build {snap.name}" if snap.pack
                                  else "snapcraft")
        reporter.detail(f"build it with: cd {snap.path} && {how}")
        return None
    return build(snap, reporter)


def build(snap, reporter, extra=()):
    """Build the project, and find what it produced.

    Usually that means `snapcraft pack`. A record naming a `pack` file is a
    project that assembles its own prime/ tree -- because snapcraft cannot
    build a core24 snap without a backend, and a project whose whole content
    is an upstream binary being restaged does not need one -- so that file's
    build(p) is called instead. A record naming a `build_with` command runs
    that; running snapcraft at either would fail on a recipe that is not
    there.
    """
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

        if reporter.captures_output:
            done = buildlib.stream(command, reporter, cwd=directory, shell=shell)
        else:
            with reporter.suspended():
                done = subprocess.run(command, cwd=directory, shell=shell)
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
