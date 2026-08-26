"""Upstreams that are not a GitHub release.

Most of what this tool packages is a GitHub release, and `github.py` reads
those. Six of the projects here are not: Discord publishes no index at all
and only a download endpoint that redirects to the current build; Emacs and
ffmpeg publish release tarballs into a directory listing; Signal, Sublime
Text and Unity publish their .deb nowhere but their own apt repository.

Two more build from the archive GitHub rolls out of a tag rather than from
anything attached to the release, which has no asset to match a pattern
against. And a snap made from a file somebody handed over has no upstream at
all -- what it watches is the folder the file sits in.

Rather than a resolver per project -- which is what the updater this replaces
had, and why it knew about twenty-one projects by name and nothing else --
each of those is one of five shapes, and the shape is written down in the
record:

    "upstream": {"kind": "apt", "base": "...", "index": "...",
                 "package": "signal-desktop"}
    "upstream": {"kind": "index", "url": "https://ffmpeg.org/releases/",
                 "pattern": "ffmpeg-(\\\\d+\\\\.\\\\d+(?:\\\\.\\\\d+)?)\\\\.tar\\\\.xz",
                 "asset": "ffmpeg-{version}.tar.xz"}
    "upstream": {"kind": "redirect", "url": "https://discord.com/api/...",
                 "pattern": "/apps/linux/([^/]+)/", ...}
    "upstream": {"kind": "tag-archive", "repo": "mpv-player/mpv", "prefix": "v",
                 "asset": "mpv-{version}.tar.gz", "download": "...{tag}.tar.gz"}
    "upstream": {"kind": "local", "glob": "discord-*.deb"}

A record with no `upstream` is a GitHub release, which is the ordinary case
and needs nothing written down beyond the repository. Anything here can be
edited into a record for an upstream nobody has met yet, which is the whole
reason it is a table and not a function per project.

The verifiers are the same idea. What a project checks a download against
before its checksum is trusted -- a detached GPG signature, a file that must
be inside the tarball -- is named in the record rather than coded per project:

    "verify": {"kind": "gpg", "suffix": ".sig"}
    "verify": {"kind": "tar-member", "member": "mpv-{version}/MPV_VERSION"}
"""

import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from . import github, local
from .net import NetworkError, download, get_text, head_location
from .versions import apt_stanza, newest


@dataclass(frozen=True)
class Release:
    """What upstream currently offers, resolved.

    version  as the packaging metadata spells it (no v prefix, no -stable)
    asset    the upstream file name
    local    what the build expects that file to be called in the project,
             where upstream's name is not it
    glob     matches the artifacts of every version, for cleaning up the old
    url      where to fetch it
    sha      upstream's sha256, empty when upstream publishes none
    """

    version: str
    url: str = ""
    asset: str = ""
    local: str = ""
    glob: str = ""
    sha: str = ""
    tag: str = ""
    # Set when the file is already here, so an update is the rewrite alone.
    path: str = ""

    def __post_init__(self):
        if self.asset and not self.local:
            object.__setattr__(self, "local", self.asset)


def _fill(template, **values):
    return template.format(**values) if template else ""


def _release(config, version, asset, url, **extra):
    """A Release, with the parts every shape reads out of its config.

    `local` and `glob` are about what the project does with the file rather
    than about the upstream that publishes it -- what the build opens it as,
    and what matches it in every other release -- so they come from the
    record the same way for all of them.
    """
    return Release(version=version, asset=asset, url=url,
                   local=_fill(config.get("local", ""), version=version),
                   glob=config.get("glob", ""), **extra)


# --- the shapes -------------------------------------------------------------

def _apt(config, want):
    """A release out of an apt Packages index.

    An index carries an SHA256 for every package in it, so these never
    download anything to work out a checksum -- it comes from the publisher.
    """
    base, index = config["base"], config["index"]
    version, filename, sha = apt_stanza(index, config["package"], want or "")
    return _release(config, version, filename.rsplit("/", 1)[1],
                    f"{base}/{filename}", sha=sha)


def _index(config, want):
    """A release read off a directory listing of every release ever published.

    `pattern` captures the version out of the listing; the newest of what it
    finds is the release, unless one was pinned. Release candidates and
    prereleases are excluded by the pattern rather than by a rule here --
    ffmpeg names its `ffmpeg-N.N-rcN`, which `\\d+\\.\\d+(\\.\\d+)?` does not
    match.
    """
    version = want
    if not version:
        listing = get_text(config["url"])
        version = newest(re.findall(config["pattern"], listing))
    if not version:
        raise NetworkError(f"nothing matching {config['pattern']} in "
                           f"{config['url']}")
    asset = _fill(config["asset"], version=version)
    url = _fill(config.get("download", ""), version=version, asset=asset) \
        or config["url"].rstrip("/") + "/" + asset
    return _release(config, version, asset, url)


def _redirect(config, want):
    """A version read off where a download endpoint redirects to.

    Discord publishes no index; the endpoint answers with a 302 to the
    current build and the version is in the path it lands on.
    """
    version = want
    if not version:
        location = head_location(config["url"])
        found = re.search(config["pattern"], location)
        if not found:
            raise NetworkError(f"could not read a version out of {location}")
        version = found.group(1)
    asset = _fill(config["asset"], version=version)
    return _release(config, version, asset,
                    _fill(config["download"], version=version, asset=asset))


def _tag_archive(config, want):
    """The archive GitHub generates from a tag, rather than a release asset.

    mpv and RetroArch publish no source tarball of their own: the release is
    the tag, and what distributions build is the archive GitHub rolls from
    it. There is nothing attached to the release to match a pattern against,
    so the URL is built out of the tag -- and because a tag that does not
    exist answers with GitHub's 404 page rather than an error, these are the
    projects that want a `verify` of `tar-member`.
    """
    prefix = config.get("prefix", "")
    tag = f"{prefix}{want}" if want else github.latest_tag(config["repo"])
    version = want or github.version_of(tag)
    asset = _fill(config["asset"], version=version, tag=tag)
    return _release(config, version, asset,
                    _fill(config["download"], version=version, tag=tag,
                          asset=asset), tag=tag)


def _local(config, want, directory):
    """The package file sitting in the project directory.

    For a snap made from a file rather than from a release. There is no
    upstream to ask, because this tool cannot know where the file came from
    -- so what it watches is the folder, which is the one thing it can see.
    Drop a newer `.deb` in beside the project and this reports it, the same
    way a repository reports a release.
    """
    if directory is None:
        raise NetworkError("a local upstream is relative to a project "
                           "directory, and this record names none")
    if want:
        wanted = [f for f in local.find(directory, config.get("glob"))
                  if f.version == want]
        if not wanted:
            raise NetworkError(f"no {want} in {directory}")
        found = wanted[0]
    else:
        found = local.newest(directory, config.get("glob"))
    if found is None:
        pattern = config.get("glob") or "a package"
        raise NetworkError(f"no {pattern} in {directory} -- put one there, or "
                           f"`snapkit create` it from wherever it is")
    return _release(config, found.version, found.name, found.path.as_uri(),
                    path=str(found.path))


SHAPES = {"apt": _apt, "index": _index, "local": _local,
          "redirect": _redirect, "tag-archive": _tag_archive}

# The shapes about a directory rather than the network, so they need one.
NEEDS_DIRECTORY = ("local",)


def resolve(config, want=None, directory=None):
    """What this upstream offers now. Raises NetworkError if it cannot say."""
    kind = config.get("kind", "")
    shape = SHAPES.get(kind)
    if shape is None:
        raise NetworkError(f"no such upstream kind: {kind or '(none)'} "
                           f"(try: {', '.join(sorted(SHAPES))})")
    if kind in NEEDS_DIRECTORY:
        return shape(config, want, directory)
    return shape(config, want)


def label(snap, folder="this folder"):
    """Where a snap's releases come from, in a few words.

    One answer, because `list`, `search` and the dashboard's inspector all
    ask it and three of them drifting apart is how a listing ends up naming
    something that is not what gets checked.

    The order matters: a shape the record names beats a repository. A
    repository can also have been guessed off a README and left inert -- as
    Signal's is, whose .deb exists only in its apt repository whatever its
    README links to -- and naming that in preference to the upstream
    actually being resolved would name the wrong thing.
    """
    kind = snap.upstream.get("kind", "")
    if kind == "local":
        return folder
    if kind:
        where = (snap.upstream.get("package") or snap.upstream.get("repo")
                 or snap.upstream.get("url", ""))
        return f"{kind}: {where}" if where else kind
    return snap.repo or ""


def manifest_sha(url, asset, required=True):
    """One entry out of a `<sha256>  <name>` checksum manifest.

    Where upstream publishes one of these there is nothing to download to
    work out a checksum: it goes into the recipe as given and snapcraft
    checks the source against it at build time. Names are written both bare
    and as ./<name>.

    Not every project publishes a manifest for every release, hence
    `required`: a missing one leaves the sha empty, which means the download
    is checked against nothing rather than against something wrong.
    """
    try:
        lines = get_text(url).splitlines()
    except NetworkError:
        if required:
            raise
        return ""
    for line in lines:
        cells = line.split()
        if len(cells) == 2 and cells[1].lstrip("./") == asset:
            return cells[0]
    if required:
        raise NetworkError(f"no checksum for {asset} in {url}")
    return ""


# --- what a download is checked against -------------------------------------

def _gpg(config, path, release):
    """Check a detached signature published next to the download.

    Where upstream publishes no checksum file, the sha256 that goes into the
    recipe is computed from this download, and says only that the bytes
    arrived intact. The signature is the only thing that says they are the
    bytes upstream released. A release key that is not already in the keyring
    is reported as unverified rather than silently passed -- fetching a key
    to check a signature with is not a check.
    """
    if not shutil.which("gpg"):
        return "gpg: not installed, signature not checked"
    signature = Path(str(path) + config.get("suffix", ".sig"))
    try:
        download(release.url + config.get("suffix", ".sig"), signature)
    except NetworkError:
        return "gpg: upstream published no signature for this release"
    try:
        done = subprocess.run(["gpg", "--verify", str(signature), str(path)],
                              capture_output=True)
    finally:
        signature.unlink(missing_ok=True)
    if done.returncode == 0:
        return "gpg: signature verified"
    return "gpg: NOT verified (release key not in your keyring) -- continuing"


def _tar_member(config, path, release):
    """Check that a file that must be in this tarball is in it.

    For the projects built from a GitHub tag archive rather than a release
    tarball upstream rolled: a tag that does not exist gives back GitHub's
    404 page, not an error, and a 404 page has no checksum to disagree with.
    """
    member = _fill(config["member"], version=release.version, tag=release.tag)
    try:
        with tarfile.open(path) as tar:
            tar.getmember(member)
    except (tarfile.TarError, KeyError):
        raise NetworkError(f"{release.url} is not a {release.version} tarball: "
                           f"it does not contain {member}")
    return f"archive contains {member}"


VERIFIERS = {"gpg": _gpg, "tar-member": _tar_member}


def verify(config, path, release):
    """Check a download before its checksum is trusted; returns what it found.

    Raises NetworkError when the download is demonstrably not the release it
    claims to be. A check that could not be carried out -- no gpg, no
    signature published -- comes back as a sentence rather than an exception,
    because "not checked" and "checked and wrong" are different things and
    only one of them should stop a build.
    """
    if not config:
        return ""
    verifier = VERIFIERS.get(config.get("kind", ""))
    if verifier is None:
        raise NetworkError(f"no such verifier: {config.get('kind') or '(none)'} "
                           f"(try: {', '.join(sorted(VERIFIERS))})")
    return verifier(config, path, release)
