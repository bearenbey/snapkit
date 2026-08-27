"""A repository URL, and what that repository publishes."""

import re
from dataclasses import dataclass, field

from .net import NetworkError, get_text, head_location

# Repository names GitHub accepts: alphanumerics, dot, dash, underscore.
_NAME = r"[A-Za-z0-9._-]+"
_REPO = re.compile(rf"^(?:https?://)?(?:www\.)?(?:github\.com/)?({_NAME})/({_NAME}?)/?$")

# What GitHub appends to a repository's own description in og:description.
_BOILERPLATE = re.compile(
    r"\s*(?:-\s*)?Contribute to .* development by creating an account on GitHub\.?\s*$")

# Read the licence file itself: scraping the repo page got one in four right.
LICENCE_FILES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "COPYING")

# Ordered: longer GPL names before the shorter ones they contain.
LICENCE_SIGNS = (
    (r"^\s*MIT License", "MIT"),
    (r"Apache License\s*\n?\s*Version 2\.0", "Apache-2.0"),
    (r"GNU AFFERO GENERAL PUBLIC LICENSE\s*\n?\s*Version 3", "AGPL-3.0"),
    (r"GNU LESSER GENERAL PUBLIC LICENSE\s*\n?\s*Version 2\.1", "LGPL-2.1"),
    (r"GNU LESSER GENERAL PUBLIC LICENSE\s*\n?\s*Version 3", "LGPL-3.0"),
    (r"GNU GENERAL PUBLIC LICENSE\s*\n?\s*Version 3", "GPL-3.0"),
    (r"GNU GENERAL PUBLIC LICENSE\s*\n?\s*Version 2", "GPL-2.0"),
    (r"Mozilla Public License Version 2\.0", "MPL-2.0"),
    (r"unencumbered software released into the public domain", "Unlicense"),
    (r"^\s*ISC License", "ISC"),
    (r"Redistribution and use in source and binary forms.*?"
     r"3\.\s*Neither the name", "BSD-3-Clause"),
    (r"Redistribution and use in source and binary forms", "BSD-2-Clause"),
)


class NotFound(NetworkError):
    """The repository, release or asset asked for is not there."""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    # Filled in when the file is not just fetched under its own name.
    local: str = ""
    sha: str = ""
    glob: str = ""
    # A file that is already on disk rather than something to download.
    path: str = ""

    @property
    def lower(self):
        return self.name.lower()

    @property
    def filename(self):
        """What this file is called once it is in a project directory."""
        return self.local or self.name


@dataclass
class Release:
    repo: str
    tag: str
    version: str
    assets: list = field(default_factory=list)

    @property
    def page(self):
        return f"https://github.com/{self.repo}/releases/tag/{self.tag}"


@dataclass
class Repository:
    repo: str                 # owner/name
    description: str = ""
    license: str = ""

    @property
    def name(self):
        return self.repo.split("/")[1]

    @property
    def url(self):
        return f"https://github.com/{self.repo}"


def parse_repo(text):
    """`owner/name` out of whatever the user pasted."""
    text = (text or "").strip()
    if not text:
        raise ValueError("no repository given")
    # Query string first: .git is only at the end once ?tab=readme is gone.
    text = re.split(r"[?#]", text)[0]
    text = re.sub(r"^git@github\.com:", "", text)
    text = re.sub(r"\.git$", "", text)
    text = text.rstrip("/")
    # A deep link -- /releases, /tree/main, an issue -- still names the repo.
    found = re.search(rf"github\.com/({_NAME})/({_NAME})", text)
    if found:
        return f"{found.group(1)}/{found.group(2)}"
    found = _REPO.match(text)
    if found and found.group(2):
        return f"{found.group(1)}/{found.group(2)}"
    raise ValueError(f"not a github repository: {text}")


def describe(repo):
    """The repository's description and licence."""
    info = Repository(repo=repo)
    info.description = _description(repo)
    info.license = licence_of(repo)
    return info


def _description(repo):
    """The one-line description GitHub puts in the page's own metadata."""
    try:
        page = get_text(f"https://github.com/{repo}")
    except NetworkError:
        return ""
    found = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', page)
    if not found:
        found = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', page)
    if not found:
        return ""
    return _BOILERPLATE.sub("", _unescape(found.group(1))).strip()


def licence_of(repo):
    """The SPDX identifier of the repository's licence, or ""."""
    for name in LICENCE_FILES:
        try:
            text = get_text(f"https://raw.githubusercontent.com/{repo}/HEAD/{name}")
        except NetworkError:
            continue
        head = text[:4000]
        for pattern, spdx in LICENCE_SIGNS:
            if re.search(pattern, head, re.I | re.S):
                return spdx
        return ""      # there is a licence, but not one that is recognised
    return ""


def latest_tag(repo):
    """The newest release tag."""
    location = head_location(f"https://github.com/{repo}/releases/latest")
    if "/releases/tag/" in location:
        return _unquote(location.split("/tag/", 1)[1])
    tags = recent_tags(repo)
    if not tags:
        raise NotFound(f"{repo} has published no releases")
    return tags[0]


def recent_tags(repo, limit=30):
    """The most recent release tags, newest first, off the atom feed."""
    try:
        feed = get_text(f"https://github.com/{repo}/releases.atom")
    except NetworkError as exc:
        raise NotFound(f"{repo}: no releases feed ({exc})") from exc
    seen, tags = set(), []
    # Only the entries' own links. The release notes travel in the same feed
    # with their markup escaped, so a looser match walked straight out of a
    # tag and into `&quot;&gt;Releases page&lt;/a&gt;`.
    for match in re.findall(r'href="[^"]*?/releases/tag/([^"]+)"', feed):
        tag = _unquote(match)
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags[:limit]


def assets(repo, tag):
    """Everything attached to one release."""
    url = f"https://github.com/{repo}/releases/expanded_assets/{_quote(tag)}"
    try:
        page = get_text(url)
    except NetworkError as exc:
        raise NotFound(f"{repo} has no release {tag}") from exc

    found, seen = [], set()
    for path in re.findall(r'href="(/[^"]*?/releases/download/[^"]+)"', page):
        name = _unquote(path.rsplit("/", 1)[1])
        if name in seen:
            continue
        seen.add(name)
        found.append(Asset(name=name, url=f"https://github.com{path}"))
    return found


def release(repo, tag=None):
    """One release, resolved: its tag, the version it stands for, its files."""
    tag = tag or latest_tag(repo)
    return Release(repo=repo, tag=tag, version=version_of(tag),
                   assets=assets(repo, tag))


def version_of(tag):
    """The version a tag stands for."""
    version = tag.strip()
    version = re.sub(r"^(?:v|release[-/]?|rel[-/]?)", "", version, flags=re.I)
    version = re.sub(r"[-_](?:stable|release|final)$", "", version, flags=re.I)
    # A monorepo tag such as helium-0.15.6 or app@1.2.3.
    found = re.match(r"^[A-Za-z][A-Za-z0-9._-]*?[-@/](\d.*)$", version)
    if found:
        version = found.group(1)
    return version or tag


def _quote(text):
    return text.replace("#", "%23").replace(" ", "%20")


def _unquote(text):
    return re.sub(r"%([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), text)


def _unescape(text):
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(entity, char)
    return text
