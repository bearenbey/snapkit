"""Upstreams that are not a GitHub release."""

import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from string import Formatter

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


# --- saying which shape, and what it needs to be told ------------------------

class BadUpstream(ValueError):
    """The settings given for an upstream do not describe one."""


@dataclass(frozen=True)
class Shape:
    """One kind of upstream, and what has to be said to point at it."""

    kind: str
    summary: str
    keys: dict                                  # key -> what it is for
    required: tuple = ()
    defaults: dict = field(default_factory=dict)
    # key -> the placeholders this shape can fill in, for the ones templated.
    templates: dict = field(default_factory=dict)
    patterns: tuple = ()                        # keys that are regexes
    example: str = ""

    @property
    def optional(self):
        return tuple(k for k in self.keys if k not in self.required)


# _release reads these for every shape, so every shape accepts them.
COMMON = {
    "local": "what to save the file as here, if not its own name",
    "glob": "matches every version of that file, so the old one is cleaned up",
}

SPECS = (
    Shape(kind="apt",
          summary="the newest amd64 stanza in an apt Packages index",
          keys={"base": "the repository root that Filename: is relative to",
                "package": "the Package: name to take out of the index",
                "index": "the Packages file itself"},
          required=("base", "package", "index"),
          defaults={"index": "{base}/dists/stable/main/binary-amd64/Packages"},
          templates={"local": ("version",)},
          example="snapkit track signal-desktop apt"
                  " base=https://updates.signal.org/desktop/apt"
                  " package=signal-desktop"),

    Shape(kind="index",
          summary="the newest version named in a listing of every release",
          keys={"url": "the listing to read",
                "pattern": "a regex with one group around the version",
                "asset": "the file to fetch, once the version is known",
                "download": "where that file is, if not under the listing"},
          required=("url", "pattern", "asset"),
          templates={"asset": ("version",),
                     "download": ("version", "asset"),
                     "local": ("version",)},
          patterns=("pattern",),
          example="snapkit track emacs index url=https://ftp.gnu.org/gnu/emacs/"
                  " 'pattern=emacs-(\\d+\\.\\d+)\\.tar\\.xz\"'"
                  " asset=emacs-{version}.tar.xz"),

    Shape(kind="redirect",
          summary="the version in the URL a download endpoint redirects to",
          keys={"url": "the endpoint to ask, without following it",
                "pattern": "a regex with one group, against the redirect target",
                "asset": "the file that version is published as",
                "download": "where to fetch it from"},
          required=("url", "pattern", "asset", "download"),
          templates={"asset": ("version",),
                     "download": ("version", "asset"),
                     "local": ("version",)},
          patterns=("pattern",),
          example="snapkit track discord redirect"
                  " 'url=https://discord.com/api/download?platform=linux&format=deb'"
                  " 'pattern=/apps/linux/([^/]+)/' asset=discord-{version}.deb"
                  " download=https://dl.discordapp.net/apps/linux/{version}/{asset}"),

    Shape(kind="tag-archive",
          summary="a GitHub tag, for a project that attaches no source tarball",
          keys={"repo": "owner/name on GitHub",
                "prefix": "what the tag puts before the version, usually v",
                "asset": "what to call the archive here",
                "download": "where GitHub serves that tag's archive"},
          required=("repo", "asset", "download"),
          templates={"asset": ("version", "tag"),
                     "download": ("version", "tag", "asset"),
                     "local": ("version",)},
          example="snapkit track mpv tag-archive repo=mpv-player/mpv prefix=v"
                  " asset=mpv-{version}.tar.gz download=https://github.com/"
                  "mpv-player/mpv/archive/refs/tags/{tag}.tar.gz"),

    Shape(kind="local",
          summary="the newest package file sitting in the project folder",
          keys={},
          templates={"local": ("version",)},
          example="snapkit track demo local glob='demo_*_amd64.deb'"),
)

SPEC = {shape.kind: shape for shape in SPECS}


def parse_pairs(words):
    """`key=value` words as a dict, in the order they were given."""
    values = {}
    for word in words:
        key, sign, value = word.partition("=")
        if not sign or not key.strip():
            raise BadUpstream(f"{word!r} is not key=value -- an upstream is "
                              f"described as name=value, name=value")
        values[key.strip()] = value.strip()
    return values


def configure(kind, values):
    """A checked upstream config, or a refusal that says what is missing."""
    shape = SPEC.get(kind)
    if shape is None:
        raise BadUpstream(f"no such upstream kind: {kind or '(none)'} "
                          f"(try: {', '.join(sorted(SPEC))})")
    known = {**shape.keys, **COMMON}
    for key in values:
        if key not in known:
            raise BadUpstream(f"{kind} has no {key!r} setting -- it takes "
                              f"{', '.join(sorted(known))}")

    config = {"kind": kind}
    config.update({key: value for key, value in values.items() if value})
    for key, template in shape.defaults.items():
        if not config.get(key):
            try:
                config[key] = template.format(**config)
            except KeyError:
                pass                       # a missing part; `required` says so

    # A key with a default only goes missing when what it is built from did.
    missing = [key for key in shape.required
               if not config.get(key) and key not in shape.defaults]
    missing = missing or [key for key in shape.required if not config.get(key)]
    if missing:
        raise BadUpstream(
            f"{kind} needs {', '.join(missing)}\n"
            + "\n".join(f"           {key} is {known[key]}" for key in missing)
            + f"\n\n           {shape.example}")

    _check_patterns(shape, config)
    _check_templates(shape, config)
    order = ("kind", *shape.keys, *COMMON)
    return {key: config[key] for key in order if key in config}


def _check_patterns(shape, config):
    """A regex that does not compile, or does not capture, caught here."""
    for key in shape.patterns:
        text = config.get(key, "")
        if not text:
            continue
        try:
            compiled = re.compile(text)
        except re.error as exc:
            raise BadUpstream(f"{key}={text!r} is not a regular "
                              f"expression: {exc}") from exc
        if compiled.groups != 1:
            raise BadUpstream(
                f"{key}={text!r} has {compiled.groups} capturing groups, and "
                f"{shape.kind} reads the version out of exactly one -- put "
                f"( ) around the version and nothing else")


def _check_templates(shape, config):
    """A {placeholder} the shape cannot fill in, caught here rather than later."""
    for key, allowed in shape.templates.items():
        text = config.get(key, "")
        if not text:
            continue
        try:
            fields = [f for _lit, f, _spec, _conv in Formatter().parse(text)]
        except ValueError as exc:
            raise BadUpstream(f"{key}={text!r} has an unmatched brace: "
                              f"{exc}") from exc
        for name in fields:
            if name is None or name in allowed:
                continue
            offered = ", ".join("{%s}" % one for one in allowed) or "nothing"
            raise BadUpstream(
                f"{key}={text!r} asks for {{{name}}}, which {shape.kind} "
                f"cannot fill in -- it fills in {offered}")


def summarise(config):
    """An upstream config as the line `show` and `track` print."""
    kind = config.get("kind", "")
    rest = " ".join(f"{key}={value}" for key, value in config.items()
                    if key != "kind")
    return f"{kind} {rest}".strip() or "(none)"


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
