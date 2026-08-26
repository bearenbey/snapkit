"""Upstreams that are not a GitHub release."""

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
    """What upstream currently offers, resolved."""

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
    """A Release, with the parts every shape reads out of its config."""
    return Release(version=version, asset=asset, url=url,
                   local=_fill(config.get("local", ""), version=version),
                   glob=config.get("glob", ""), **extra)


# --- the shapes -------------------------------------------------------------

def _apt(config, want):
    """A release out of an apt Packages index."""
    base, index = config["base"], config["index"]
    version, filename, sha = apt_stanza(index, config["package"], want or "")
    return _release(config, version, filename.rsplit("/", 1)[1],
                    f"{base}/{filename}", sha=sha)


def _index(config, want):
    """A release read off a listing of every release published."""
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
    """A version read off where a download endpoint redirects to."""
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
    """The archive GitHub generates from a tag, rather than a release asset."""
    prefix = config.get("prefix", "")
    tag = f"{prefix}{want}" if want else github.latest_tag(config["repo"])
    version = want or github.version_of(tag)
    asset = _fill(config["asset"], version=version, tag=tag)
    return _release(config, version, asset,
                    _fill(config["download"], version=version, tag=tag,
                          asset=asset), tag=tag)


def _local(config, want, directory):
    """The package file sitting in the project directory."""
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
    """Where a snap's releases come from, in a few words."""
    kind = snap.upstream.get("kind", "")
    if kind == "local":
        return folder
    if kind:
        where = (snap.upstream.get("package") or snap.upstream.get("repo")
                 or snap.upstream.get("url", ""))
        return f"{kind}: {where}" if where else kind
    return snap.repo or ""


def manifest_sha(url, asset, required=True):
    """One entry out of a `<sha256>  <name>` checksum manifest."""
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
    """Check a detached signature published next to the download."""
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
    """Check that a file that must be in this tarball is in it."""
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
    """Check a download before its checksum is trusted."""
    if not config:
        return ""
    verifier = VERIFIERS.get(config.get("kind", ""))
    if verifier is None:
        raise NetworkError(f"no such verifier: {config.get('kind') or '(none)'} "
                           f"(try: {', '.join(sorted(VERIFIERS))})")
    return verifier(config, path, release)
