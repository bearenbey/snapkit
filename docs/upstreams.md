# Where releases are looked for

## Upstreams that are not a release


A folder is the honest answer when a file was handed to you, but it is the
wrong one when the version really is published somewhere, just not as a
GitHub release. Eight of the projects packaged here are like that. Signal,
Sublime Text and Unity Hub publish into apt repositories of their own. Emacs
and ffmpeg publish a directory listing of every release there has ever been.
Discord answers a download endpoint with a redirect and puts the version in
the path it redirects to. mpv and RetroArch do use GitHub, but attach no
source tarball, so what there is to fetch is the archive GitHub rolls from a
tag.

`snapkit track` says which of those a snap is, and what to read:

```console
$ snapkit track sublime-text apt base=https://download.sublimetext.com \
      package=sublime-text index=https://download.sublimetext.com/apt/stable/Packages
==> sublime-text: apt base=https://download.sublimetext.com package=sublime-text …
    upstream has 4200
    which it publishes as sublime-text_build-4200_amd64.deb
==> sublime-text is tracked against apt: sublime-text
```

The kinds, and what each one needs:

| kind | how it finds the version | needs |
| --- | --- | --- |
| `apt` | the newest amd64 stanza in a `Packages` index, ordered the way dpkg orders versions | `base`, `package`, and `index` if it is not the Debian default path |
| `index` | a regex with one group, over a listing of every release | `url`, `pattern`, `asset` |
| `redirect` | a HEAD request, and a regex over where it redirects to | `url`, `pattern`, `asset`, `download` |
| `tag-archive` | the newest tag on a GitHub repository | `repo`, `asset`, `download`, and `prefix` if the tag has one |
| `local` | the newest matching file in the project folder | nothing, though `glob` narrows it |

`asset` and `download` are templates: `{version}`, `{tag}` and `{asset}` are
filled in once the version is known. `glob` matches every version of the file
so the superseded one is cleaned up, and `local` renames it on the way in, for
the upstreams whose filename changes every release.

`snapkit track kinds` prints that table with a worked example under each one,
and `snapkit track <name>` with nothing after it says what a snap is tracked
against now and what that has at this moment. The dashboard takes the same
words behind `t`.

The settings are checked before they are written down, and then resolved
once. A regex that does not compile, a regex with two capturing groups where
one version is wanted, a `{tag}` asked of a shape that has no tag to give: all
three are a refusal at the prompt rather than a `KeyError` a year later. And
an upstream that resolves to nothing leaves the record exactly as it was:

```console
$ snapkit track emacs index url=https://ftp.gnu.org/gnu/emacs/ \
      'pattern=emacs-(\d+)\.tar\.gz' asset=emacs-{version}.tar.gz
==> emacs: index url=https://ftp.gnu.org/gnu/emacs/ pattern=emacs-(\d+)\.tar\.gz …
snapkit: emacs was left as it was, because that upstream did not resolve:
           nothing matching emacs-(\d+)\.tar\.gz in https://ftp.gnu.org/gnu/emacs/

           `snapkit track kinds` says what index takes; --force writes it
           down unresolved
```

That refusal is the point of the command. An upstream written down without
being tried reads as "up to date" for as long as nobody looks, which is the
one failure mode a version checker must not have. `--force` is there for the
case where the endpoint is down rather than the setting wrong, and it says so
in the output rather than passing silently.

Two other forms: `snapkit track <name> repo owner/name` puts a snap back on
GitHub releases, reading the release to relearn which file to take and what
its name will look like next time, and `snapkit track <name> none` stops it
being checked against anything at all.

## Updating


`snapkit check` asks every registered repository what it has now. `snapkit
update <name>` moves that snap onto it and rebuilds.

Upstreams rename their assets. btop's musl build went from `.tbz` to
`.tar.gz` in a patch release. When the stored pattern stops matching, the
best asset of the same kind is taken instead and you are told that it
happened, rather than the update failing or quietly packaging something else.

There are two things a project can mean by "the source", and an update has to
know which. Most recipes name a URL and let snapcraft fetch it at build time:
an update repoints that line, rewrites its checksum, and leaves every other
edit in the file alone. The rest open a file that has to be sitting in the
project directory before the build can start, so an update downloads it
there, drops the superseded one, and replaces the version wherever the
project spells it out: the recipe, the overlay metadata, the README.
Every line it rewrites is printed, because a bump should be reviewable
without diffing afterwards, and that matters most when a version string turns
up somewhere nobody expected it.

Where upstream publishes no checksum, what the download is checked against is
also in the record: a detached GPG signature for Emacs and ffmpeg, and for
the tag archives a file that has to be inside the tarball. A tag that does
not exist answers with GitHub's 404 page rather than an error, and a 404 page
has no checksum to disagree with.

A version in the register is a cache, not the authority. A project can be
edited by hand between builds, so opening the register re-reads the version
from each project that is still on disk and puts the record back in line:
`list` and `check` cannot report a stale one. A record whose project
directory is gone keeps the last version it was known to be on, because there
is nothing better to say. A project whose artifact has gone missing reads as
behind rather than as up to date and broken, because fetching it back is
exactly what an update does.

## Packaging something again


The register holds the recipe and keeps a copy of the icon beside it, so a
snap that has been made once can be made again from what is written down --
no repository, no release lookup, nothing downloaded by this tool. snapcraft
still fetches the source the recipe names and checks it against the checksum
that was written in at the time.

```text
snapkit search monitor         find it by name, repository or summary
snapkit package btop           build it from the register
```

`package` takes whatever you have: the name, the repository, a pasted URL, or
a word from the summary. Delete the project directory and `package` writes it
back out, `snapcraft.yaml` and icon and all.

Giving `create` a repository it already knows does the same thing rather than
telling you off:

```console
$ snapkit create https://github.com/aristocratos/btop
    aristocratos/btop is already registered as btop (1.4.7) -- building it
      from the register
    (--name makes a second one; `snapkit update btop` looks for a newer release)
```

---

[Back to the README](../README.md)
