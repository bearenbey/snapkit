# snapkit

**Turn a GitHub release into a snap, and keep it that way.**

![Linux](https://img.shields.io/badge/Linux-amd64-informational)
![base](https://img.shields.io/badge/snap%20base-core24-informational)
![confinement](https://img.shields.io/badge/confinement-classic-informational)
![deps](https://img.shields.io/badge/python%20deps-rich-informational)

> [!IMPORTANT]
> **This project is co-authored with Claude, Anthropic's Opus 5 model, and
> roughly a quarter of what you are reading was written by it.**
>
> The measurable part: about 1,800 of the 16,100 lines of source in this tree
> were written by Claude from scratch. That is the shared recipe database and
> its commands, eight of the twenty-one snapcraft recipes, and nine of the
> `pack.py` build scripts. On top of that it rewrote every comment and
> docstring in the tree, most of this README, and the dashboard's build and
> install handling.
>
> The parts that are not Claude's: the design of the tool, the register and
> the update model, and the packaging decisions behind all twenty-one snaps.
> Those came first and the rest was built onto them.
>
> The quarter counts work Claude did and can point to. It has no way of
> knowing whether anything here was written with help before that, so read it
> as the least AI went into this rather than the most.

Make a snap package out of a GitHub repository, or out of a `.deb` you
already have, and keep it that way.

Give it a repository. It finds the newest release, works out which of the
files attached to it can actually be packaged, downloads that file, opens it
to see where the program and its desktop entry and icon really are, writes a
`snapcraft.yaml` around what it found, and builds the snap.

```console
$ snapkit create aristocratos/btop
==> looking up aristocratos/btop
    newest release v1.4.7 (1.4.7), 11 files attached
    chose btop-x86_64-unknown-linux-musl.tar.gz -- a prebuilt archive,
      x86_64, linux, statically linked against musl
==> fetching btop-x86_64-unknown-linux-musl.tar.gz
    sha256 5099054dd6a101bd12eb6ff3702a9a6a3f57aaa27923a0da478ae5b517faf335
==> opening the payload
    command bin/btop
    desktop entry btop.desktop
    icon copied to snap/gui/btop.svg
    looks like terminal
==> wrote ~/.local/share/snapkit/projects/btop/snap/snapcraft.yaml
==> registered btop 1.4.7
==> built btop_1.4.7_amd64.snap (1 MB)
```

## Contents

| | |
| --- | --- |
| [The dashboard](#the-dashboard) | one screen for everything, and what the keys do |
| [Why it opens the payload](#why-it-opens-the-payload) | why the file is read rather than guessed at |
| [What it can package](#what-it-can-package) | debs, archives, AppImages, and what is refused |
| [When there is no repository](#when-there-is-no-repository) | packaging a file you already have |
| [Upstreams that are not a release](#upstreams-that-are-not-a-release) | apt, a listing, a redirect, a bare tag |
| [The shared database](#the-shared-database) | recipes published for another machine to build |
| [The register](#the-register) | where records live, and why it is a directory |
| [Projects you already have](#snap-projects-you-already-have) | importing packaging that predates this |
| [Packaging something again](#packaging-something-again) | rebuilding from the register alone |
| [Updating](#updating) | how a new release reaches the packaging |
| [Architectures](#architectures) | how it knows which build is the one to take |
| [Commands](#commands) | the whole interface, in one list |
| [Tests](#tests) | what is covered, and the bugs behind it |
| [Building this](#building-this) | making the snapkit snap itself |
| [Caveats](#caveats) | what it does not do |

## The dashboard

Run it with no arguments for the dashboard. Up and down move through the
list, `enter` shows a record in full, `t` says where the selected snap's
releases should be looked for, and `q` is the only thing that quits.

```text
╭──────────────────────────────────────────────────────────────────────────────────╮
│ ◆ SNAPKIT   ▪ 21 registered  ● 18 current  ▲ 2 behind  ◐ 1 in flight             │
╰──────────────────────────────────────────────────────────────────────────────────╯
╭──────────── registered  1-6 of 21 ─────────────╮╭────────── inspector ───────────╮
│     NAME        BUILT      UPSTREAM  STATUS    ││ floorp                         │
│     btop        1.4.7      1.4.7     ● up to…  ││ ▲ UPDATE AVAILABLE             │
│     emacs       30.2       30.2      ● up to…  ││                                │
│ ▸   floorp      12.17.0    13.0.0    ▲ UPDATE  ││ 12.17.0  →  13.0.0             │
│     helium      0.15.6.1   0.15.6.1  ● up to…  ││                                │
│     mpv         0.41.0     0.41.0    ████▊░░░  ││ built    12.17.0               │
│     nvim        0.12.5     0.12.5    ● up to…  ││ kind     archive               │
╰────────────────────────────────────────────────╯╰────────────────────────────────╯
  [↑↓] move  [n] new or find  [r] recheck  [u] update  [b] build  [q] quit
```

`t` opens the box that says where a snap's releases come from, seeded with
what it tracks now, so changing one word is one word rather than all of them:

```text
╭─────────────────────────────────── track ────────────────────────────────────╮
│ ▸ sublime-text  apt base=https://download.sublimetext.com package=sublime-te…│
│   apt base= package=                       the newest amd64 stanza in an apt…│
│   index url= pattern= asset=               the newest version named in a lis…│
│   redirect url= pattern= asset= download=  the version in the URL a download…│
│   tag-archive repo= asset= download=       a GitHub tag, for a project that …│
│   local                                    the newest package file sitting i…│
│   repo owner/name                          the releases of a github repository│
│   none                                     stop checking it against anything │
╰──────────────────────────────────────────────────────────────────────────────╯
```

It takes the same words `snapkit track` does, and refuses the same way: the
setting is resolved before it is written down, and one that resolves to
nothing leaves the record exactly as it was. The list of kinds is built from
the shapes themselves, so a new one cannot be added and go unmentioned here.

### Building from the dashboard

`b` builds the selected project without the dashboard going anywhere. The
build's own output, ten minutes of snapcraft in most cases, is piped into the
log pane a line at a time rather than being written to the terminal, so
the list, the inspector and the status line keep working throughout and
`q`/Escape still stop the build. Cancelling kills the build rather than
waiting for it to finish.

When it is done, the dashboard asks whether to install what it made:

```text
╭─────────────────────────────── install ────────────────────────────────╮
│ install btop_1.4.7_amd64.snap?  it is not signed, so this installs     │
│ with --dangerous.  [y/N]                                               │
╰────────────────────────────────────────────────────────────────────────╯
```

`y` installs it, anything else does not, and no is the default. Installing is
the one thing here that needs root, so this is the one place that asks for it:
the dashboard steps aside for `sudo snap install --dangerous`, you type your
password on a real terminal, and it comes back when the install is done.
`--classic` is added when the recipe asks for classic confinement. While the
question is up every other key is ignored, so an arrow key cannot answer it by
accident.

Nothing is installed without being asked, and a build that is not installed is
still a build: the `.snap` is in the project directory either way.

Whatever the cursor is on is described beside the list, so reading the
register is moving the cursor rather than opening and closing records. On a
terminal too narrow for two panes the inspector goes and the list takes the
width back, along with the columns the inspector had been showing. The
columns that fit are the columns that are drawn, and the key legend shortens
before it would run off the end.

The box you type into is one box: it searches what you have already packaged
as you type, highlighting the part that matched, and only goes upstream for
something it does not recognise.

```text
▸ find or add  monitor
  ▸ btop                1.4.7             aristocratos/btop
  enter builds the highlighted one from the register -- no download, no lookup
```

## Why it opens the payload

Because guessing is wrong often enough to be useless. A Go project's `.deb`
puts its binary in `usr/bin`; an Electron project's puts it in `opt/Name`;
the desktop entry that names it is somewhere else again. The file has to be
downloaded to build anyway, so it is opened first and the recipe is written
from what is in it rather than from a template with a hole in it.

A `.deb` is read here rather than shelled out to, since it is an `ar` archive
whose interesting member is a tar, and Python reads both. `dpkg-deb` is only
needed for the zstd-compressed ones.

## What it can package

| shape | what it does |
| --- | --- |
| `.deb` | unpacked by snapcraft and staged whole |
| `.tar.*`, `.zip` | staged, with the single top-level directory folded away |
| `.AppImage` | asked to unpack itself, and what falls out is staged |

Assets for other architectures, other operating systems, and the checksums
and signatures sitting next to them are filed out first. Everything left is
scored, and the top pick is a default rather than a decision:

- `snapkit create <repo> --asset 2` or `--asset <filename>` takes another
- in the dashboard, a release with more than one usable file stops and asks,
  showing what each one scored and why
- `snapkit show <name>` says which was taken, afterwards

Building from source is not handled. That needs a dependency list, and
guessing one from a repository nobody has read is where this would stop being
reliable.

## When there is no repository

Plenty of things worth packaging are not published as a GitHub release. Some
publish somewhere else, which is what [the next
section](#upstreams-that-are-not-a-release) is about. Others are simply
handed to you: a download somebody sent, a build off a colleague's machine, a
`.deb` that was never on the internet at all. In that case the file is
already sitting in a folder, so point `create` at the file instead of at a
repository:

```console
$ snapkit create ~/Downloads/freetube_0.25.2_amd64.deb
==> opening freetube_0.25.2_amd64.deb
    chose freetube_0.25.2_amd64.deb -- a Debian package: carries its own
      desktop entry and icon, x86_64 (amd64)
==> opening the payload
    command opt/FreeTube/freetube
    desktop entry usr/share/applications/freetube.desktop
    looks like electron, gui
    copied freetube_0.25.2_amd64.deb into ~/.local/share/snapkit/projects/freetube
==> registered freetube 0.25.2
    tracked against that folder: drop a newer freetube_*_amd64.deb in and
      `snapkit check` will say so
```

Everything that happens for a release asset happens here, minus the download.
The file is copied in beside the recipe that names it. It is named rather
than pointed at, so the project can be moved somewhere else and still build. No
`source-checksum` is written, because there is no upstream to have published
one and a checksum of a file against itself only restates that it has not
changed.

Give it a folder rather than a file and it looks in there. With nothing named
at all it asks, which is the case this exists for. The answer to "I
downloaded this, can you package it" should not be "first find me a URL":

```console
$ snapkit create
What should this snap be made from?

  packages in /home/you/Downloads
    1. freetube_0.25.2_amd64.deb  0.25.2    a Debian package: carries its own …
    2. app-2.1-x86_64.AppImage    2.1       an AppImage, which has to be unpacked

  [1-2]  package that file
  [r]    a GitHub repository -- owner/name, or a URL
  [p]    a path to a package file, or a folder to look in
  [q]    nothing
```

A snap made this way is then kept in step with its folder rather than with an
upstream. That is the honest limit of it: this tool cannot know where a file
came from, so it does not pretend to, and what it can see is the directory.
Drop a newer one in and `check` reports it like any other update; `update`
repoints the recipe, drops the superseded file and rebuilds.

`snapkit import ../some-snap --local` does the same for a project that
already exists, and without `--local` on a terminal it offers.

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

## The shared database

`snapkit db` reads a folder of recipes published in a git repository, so a
project packaged on one machine can be built on another without being
packaged again.

```sh
snapkit db                    what is published
snapkit db pull               write every project into the current directory
snapkit db pull zen godot     just those
snapkit install zen           fetch it, build it, and offer to install it
snapkit db publish <dir>      write the database out of the projects here
```

In the dashboard, `g` reads the database, says how many of its snaps are not
registered here, and asks before writing anything.

A snap is more than its `snapcraft.yaml`. Three of the twenty-one build from
the recipe alone, and the rest also need a launcher, an overlay tree, a
`pack.py` or a hook, so a project is published whole, minus the release it
was built from, the `.snap` it produced and any build tree. `index.json`
carries the file list, a sha256 and an executable bit for each, and the record
fields a project cannot tell you about itself: where its release comes from
and how an update reaches its packaging. Reading a project says what it
builds, never where it came from.

Nothing needs a token or git: the files are read over https from
`raw.githubusercontent.com`. `SNAPKIT_DB_URL` points at a different database,
which is how the tests reach one on disk.

A project whose recipe names a file too large to publish is marked incomplete
and refused by name, rather than pulled and left to fail at build time.

## The register

A directory, not a file. It lives at `~/.local/share/snapkit/`, or
`$SNAP_USER_COMMON/` inside the snap, or wherever `SNAPKIT_HOME` points:

```text
snaps/btop.json        what it is, where it came from, what to fetch
recipes/btop.yaml      the snapcraft.yaml, as text
icons/btop.svg         lifted out of the payload when it was made
```

It began as one JSON file holding all of it, which is the obvious thing and
does not last. Recipes are most of the weight. At sixteen snaps they were 78%
of the file, so every record carried several kilobytes almost nothing reads,
and a change to any field rewrote the lot. At a thousand snaps that is
5.5 MB reparsed by every command and rewritten by every `add`, which a create
does three times: about 130 ms of JSON per create, before any work.

Split up, at a thousand snaps:

| | one file | a directory |
| --- | --- | --- |
| read the register | 14.8 ms | 17.5 ms |
| change one snap | 27.7 ms | 0.1 ms |
| on disk, read to list | 5.5 MB | 0.95 MB |

Reading costs slightly more, a thousand small opens rather than one big
parse. In exchange, changing one snap no longer costs anything at all: it
writes one small file however many there are. The recipe is read only when
something asks for it, so listing a thousand snaps reads none of them.

Each piece stays a thing you can open: the record is legible JSON, the recipe
is a yaml file your editor already understands. A register from before this
changed is migrated on first use, and the old file is kept beside it as
`snapkit.json.migrated` rather than deleted.

Because it is meant to be edited, a record that cannot be read is set aside
rather than taken as the end of the register. One typo costs that record and
not the other nine hundred and ninety nine, and every command says which one
it was until it is fixed.

The register holds everything needed to rebuild:

- a project directory can be deleted and written out again from it
- paste a repository you have already used and it says so, rather than making
  a second copy of it
- removing a snap removes its record, its recipe and its icon

Two repositories whose names collapse to the same snap name are refused
rather than one replacing the other. There are a great many repositories
called `bat`. Pass `--name` to keep both; the dashboard picks a free
name and says so, rather than throwing away a build it has already made.

It is meant to be opened in an editor. So is the generated `snapcraft.yaml`:
edit it, and an update moves the version, the source URL and its checksum and
leaves the rest of your changes alone.

## Snap projects you already have

`snapkit import ../btop-snap` registers a project that exists already,
reading its recipe, version, summary and icon out of it. `./seed.py` does
that for every `*-snap` directory beside this one.

Nothing is inferred that cannot be read, and one thing that *can* be read is
deliberately not acted on: a repository worked out from a URL in a README is
recorded but left inert, because updating rewrites a recipe and repoints it
at a release. Pass `--repo owner/name` to confirm one and have that project
checked. `seed.py` holds those confirmations for the projects here.

Its table says the things a project cannot say about itself. Everything else,
the version, summary, licence, icon and the recipe, is read off the project:

| field | what it says |
| --- | --- |
| `repo` | the GitHub repository it packages, where that is the upstream |
| `upstream` | a shape out of `sources.py`, where it is not |
| `style` | `artifact` if the build opens a file sitting in the project, `recipe` if snapcraft fetches the source itself |
| `asset_glob` | matches that file in every release, so the superseded one is cleaned up |
| `local_asset` | what the build opens it as, where upstream's name is not it |
| `source_anchor` | which `source:` line an update repoints, so a second one is left alone |
| `checksums` | where upstream publishes the checksum, when it is not beside the file |
| `verify` | what a download is checked against before it is trusted |
| `pack` | the file exposing `build(project)`, for a project that wraps the build |

`asset_glob` and `local_asset` belong to the project rather than the upstream,
so they are named once and not repeated inside `upstream`. The exception is
the `local` shape, where the glob is also how the file is found in the first
place.

A project registered this way keeps its own build. All twenty-one hand their
recipe to snapcraft, but eleven of them wrap it in a `pack.py` that does the
work a recipe cannot express, and `snapkit build` imports that and calls its
`build(project)` with a `Build`:

```python
def build(project):                       # floorp-snap/pack.py
    tarball = project.artifact("floorp-linux-x86_64.tar.xz")
    project.run("snapcraft", "pack")
    built = project.directory / f"floorp_{project.version}_amd64.snap"
    check_the_packed_snap(project, built)
    return built
```

Almost all of that work is one thing: refusing to ship a snap whose payload is
not the release the recipe claims. The rest is per project, such as reading a
version out of an `application.ini` or warning about a library a classic snap
will need from the host.

The dependency runs this way round on purpose. These scripts used to reach
the shared half of themselves along a relative path, which stops working the
moment the tool is installed as a snap, and meant every project spent four
lines on `sys.path` before it could say anything about itself. A `pack.py`
imports nothing: everything it can use is on the `project` it is handed,
including `say`, `download` and a `module()` for a build that runs to more
than one file.

Six of them are not a GitHub release at all: Discord's download redirect,
Emacs on ftp.gnu.org, ffmpeg.org, and the apt repositories Signal, Sublime
Text and Unity publish through, and two more build from the archive GitHub
rolls out of a tag rather than from anything attached to the release. Those
are shapes rather than exceptions, and the shape is in the record:

```json
"upstream": {"kind": "apt", "base": "https://updates.signal.org/desktop/apt",
             "package": "signal-desktop", "index": "..."}
```

`sources.py` holds five of them: `apt`, `index`, `redirect`, `tag-archive` and
`local` for a snap made from a file, which watches the folder that file is in
-- and a record with no `upstream` is an ordinary GitHub release. So every
one of them is checked against its real upstream rather than reported as `not
tracked upstream`:

```console
$ snapkit check
NAME                 BUILT            UPSTREAM         STATUS
btop                 1.4.7            1.4.7            up to date
defold               1.13.1           1.13.1           up to date
discord              1.0.154          1.0.155          UPDATE AVAILABLE (discord-1.0.155.deb)
emacs                31.1             31.1             up to date
signal-desktop       8.24.1           8.24.1           up to date
...
```

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

## Architectures

Nothing here is compiled, so the architecture only ever matters as a
question about somebody else's filenames: of the nine files in a release,
which one is for this machine? That question was answered with `x86_64`
written into a regex, which meant that on anything else every asset in every
release read as foreign and nothing could be packaged at all.

`snapforge/arch.py` answers it instead. It asks `dpkg --print-architecture`,
because that is what snapd agrees with, and falls back to `uname` where dpkg
is not installed. From the Debian name it builds two patterns: the spellings
that mean *this* machine, and the spellings that mean somebody else's.
Upstreams do not agree on those, so the table carries all of them:

| Debian name | what a release calls it |
| --- | --- |
| `amd64` | `amd64`, `x86_64`, `x86-64`, `x8664`, `x64`, `linux64`, `64bit` |
| `arm64` | `arm64`, `aarch64`, `armv8l`, `armv8` |
| `armhf` | `armhf`, `armv7l`, `armv7`, `armv6l`, `armv6`, `arm` |
| `i386` | `i386`, `i486`, `i586`, `i686`, `ia32`, `x86`, `32bit` |
| `ppc64el` | `ppc64el`, `ppc64le`, `powerpc64le` |
| `riscv64` | `riscv64`, `riscv` |
| `s390x` | `s390x` |
| `loong64` | `loong64`, `loongarch64` |

The spellings overlap in ways that bite. `x86` means 32-bit, but only when it
is not the front of `x86_64`, so it carries a lookahead the others do not
need. `arm` is 32-bit, but `arm64` is not `arm` with something after it. Both
were bugs here once and both are matched on boundaries now.

Set `SNAPKIT_ARCH` to answer the question differently, which is how the tests
check all of this without eight machines. Against one real btop release:

```console
$ SNAPKIT_ARCH=arm64   snapkit create aristocratos/btop --no-build
    chose btop-aarch64-unknown-linux-musl.tar.gz
$ SNAPKIT_ARCH=riscv64 snapkit create aristocratos/btop --no-build
    chose btop-riscv64-unknown-linux-musl.tar.gz
$ SNAPKIT_ARCH=s390x   snapkit create aristocratos/btop --no-build
    chose btop-s390x-ibm-linux-musl.tar.gz
```

An asset that names no architecture at all is still taken, whatever the host
is, because that is usually the only build there is.

The same question turns up in three other places. An apt index is per
architecture, so `{arch}` stays in the recorded URL rather than being filled
in when it is written down: a record travels through the shared database, and
one with `binary-amd64` baked in would have every port reading amd64's index
and quietly never updating. `{arch}` works in `url`, `asset`, `download` and
`local` too, for the upstreams that put it in a path. A written recipe names
the architecture it was written on under `platforms:`, and a packed snap is
named for it. Where an upstream publishes nothing for this machine, that is
an error and it says so, rather than fetching a build that will not run:

```console
$ SNAPKIT_ARCH=arm64 snapkit check signal-desktop unityhub sublime-text
NAME             BUILT     UPSTREAM  STATUS
signal-desktop   8.25.0    ?         .../dists/xenial/main/binary-arm64/Packages: HTTP 404
unityhub         3.21.0    3.21.0    up to date
sublime-text     4200      4200      up to date
```

Signal publishes amd64 only. Unity and Sublime Text publish arm64, and the
same three records found it without being edited.

## Commands

| command | what it does |
| --- | --- |
| `snapkit` | the dashboard |
| `snapkit create <repo>` | make a snap from a repository |
| `snapkit create ./thing.deb` | or from a file you already have |
| `snapkit create ~/Downloads` | or from whichever of those is in there |
| `snapkit create` | and with nothing named, it asks |
| `snapkit package <name\|repo>` | build one already registered, from the register |
| `snapkit search <text>` | find one by name, repository or summary |
| `snapkit list` | what is registered |
| `snapkit show <name>` | one record, in full, including the recipe |
| `snapkit check [name ...]` | what has a newer release upstream |
| `snapkit update <name> [...]` | move onto that release and rebuild |
| `snapkit update <name> --force` | redo it even if it is already current |
| `snapkit track <name>` | where its releases are looked for, and what is there |
| `snapkit track <name> <kind> ...` | look for them somewhere that is not a release |
| `snapkit track <name> repo owner/name` | put it back on GitHub releases |
| `snapkit track <name> none` | stop checking it against anything |
| `snapkit track kinds` | every kind of upstream, and what each one needs |
| `snapkit build <name>` | hand the project to snapcraft |
| `snapkit remove <name>` | forget a snap, and its recipe with it |
| `snapkit db` | what the shared recipe database holds |
| `snapkit db pull [name ...]` | write those projects here, or all of them |
| `snapkit db publish <dir>` | write the database out of the projects here |
| `snapkit install <name>` | fetch it, build it, and offer to install it |

Removing asks first, in both the dashboard and the terminal. It forgets the
record and the recipe stored with it; the project directory is left where it
is, because deleting files you may have edited is a bigger thing than
forgetting a record and should not be what one keystroke does.

A repository can be given any way you have it: `owner/name`, the browser URL,
the clone URL, or a link to a release page.

Useful flags: `--no-build` to write the project without building it, `--tag`
to pin a release, `--asset` to build from a different file in it, `--name` to
call the snap something other than the repository, `--dir` to put the project
somewhere specific, `--plain` to keep the dashboard from opening.

## Tests

```text
./tests.py            everything that needs no network
./tests.py --online   those, and the ones that talk to GitHub
```

No framework and no dependencies beyond the tool's own, so it runs anywhere
the tool does. The offline tests build their own `.deb` rather than
downloading one, so the archive reader is checked against bytes the test file
made and knows the shape of.

One function per subject: upstreams, architectures, recipes, register,
payloads, projects, checking, dashboard, updater, from_a_file, database,
tracking. A failure names the area before it names the case.

Several exist because of bugs that were in here:

- a build's output was read as text, and Python's universal newlines split on
  the `\r` a progress bar redraws itself with, so every frame of every bar
  became its own line in the log pane and buried everything else
- cancelling a build raised out of the reading loop, and `Popen.__exit__`
  closes the pipe and *waits*: stopping a ten-minute build took ten minutes.
  (The first version of that test raised `KeyboardInterrupt`, which
  `Popen.__exit__` special-cases into giving up on its own, so it passed
  whether or not the child was killed. It raises an ordinary exception now.)

- an asset name split on its separators turned `x86_64` into `x86` and `64`,
  so every 64-bit build read as 32-bit and lost to whatever named no
  architecture at all, which on btop's release page is m68k
- a top-level `icon:` was pointed inside the payload, where snapcraft never
  looks
- every dashboard action set "busy" before asking a guard that refuses
  anything already busy, so none of them ran at all
- a second repository whose name collapsed to one already registered replaced
  it, recipe and all, without a word
- the AppImage recipe globbed for `*.AppImage`, and neovim ships `.appimage`,
  so the build died at `chmod`
- the find-or-add box drew its matches into a header three rows high, so none
  of them appeared
- `check` compared the tag as well as the version, and an imported project
  has no tag, so all sixteen reported an update to the version they were on
- mpv publishes `mpv-v0.41.0-x86_64-w64-mingw32.zip`; `w64` and `mingw32`
  were in neither list, so a Windows build was offered as a Linux one
- writing a project out put its own README over the one that was there, and
  an empty `snapcraft.yaml` into a project that assembles its own tree
- emptying a recipe left its file on disk, so the next read brought the old
  one back and the register disagreed with itself
- the shape a `track` setting is checked against and the shape that reads it
  at update time were two lists that could disagree, so the tracking tests
  put every upstream in `seed.py` back through `configure()` and require it
  to come out unchanged
- `x86_64` was written into the classifier's regex, so on any other machine
  every asset in every release read as built for somewhere else and there was
  nothing left to package; `SNAPKIT_ARCH` lets the tests check eight
  architectures without eight machines
- the terminal and the dashboard each had their own copy of the rule that an
  upstream which resolves to nothing must not be written down, which is the
  one rule here that cannot be got wrong quietly: it now lives in
  `update.retrack` and both are tested against it
- `select()` watches a file descriptor and `sys.stdin` is buffered, so
  reading one character of an arrow key pulled the rest into Python's buffer
  where `select` could not see it; the sequence read as a lone Escape, and
  Escape quit, so every arrow key closed the dashboard

## Building this

```console
./build.py
sudo snap install --dangerous --classic snapkit_0.1.0_amd64.snap
```

It is a classic snap because building a snap means running snapcraft and
writing project directories wherever you keep them, and a confined snap can
do neither.

## Caveats

- The register scales; the *projects* do not, and that is the thing to watch.
  A thousand registered snaps is a megabyte. A thousand built snaps is
  however large those snaps are, and Electron apps run to a hundred megabytes
  each, all sitting in `projects/`. Nothing here prunes them.

- The 22 projects in `seed.py` name amd64 asset globs, because that is
  what this machine is. The tool is not tied to it; that list is.
- `grade: devel` on the generated recipes' sibling, because this tool is new.
- A snap built from someone else's release is not published or endorsed by
  them, and every recipe it writes says so in its description.
