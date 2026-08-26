# snapkit

Make a snap package out of a GitHub repository, or out of a `.deb` you
already have, and keep it that way.

Give it a repository. It finds the newest release, works out which of the
files attached to it can actually be packaged, downloads that file, opens it
to see where the program and its desktop entry and icon really are, writes a
`snapcraft.yaml` around what it found, and builds the snap.

```
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

Run it with no arguments for the dashboard. Up and down move through the
list, `enter` shows a record in full, and `q` is the only thing that quits.

```
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

### Building from the dashboard

`b` builds the selected project without the dashboard going anywhere. The
build's own output -- ten minutes of snapcraft, most of it -- is piped into
the log pane a line at a time rather than being written to the terminal, so
the list, the inspector and the status line keep working throughout and
`q`/Escape still stop the build. Cancelling kills the build rather than
waiting for it to finish.

When it is done, the dashboard asks whether to install what it made:

```
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

```
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

A `.deb` is read here rather than shelled out to -- it is an `ar` archive
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

Plenty of things worth packaging are not published as a GitHub release.
Discord answers a download endpoint with a redirect; Unity, Signal and
Sublime Text publish into apt repositories of their own; and some are simply
handed to you. In every one of those cases the file is already sitting in a
folder, so point `create` at the file instead of at a repository:

```
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
The file is copied in beside the recipe that names it -- by name, not by
path, so the project can be moved somewhere else and still build -- and no
`source-checksum` is written, because there is no upstream to have published
one and a checksum of a file against itself only restates that it has not
changed.

Give it a folder rather than a file and it looks in there. With nothing named
at all it asks, which is the case this exists for -- the answer to "I
downloaded this, can you package it" should not be "first find me a URL":

```
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

A snap is more than its `snapcraft.yaml` -- three of the twenty-one build from
the recipe alone, and the rest also need a launcher, an overlay tree, a
`pack.py` or a hook -- so a project is published whole, minus the release it
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

A directory, not a file — `~/.local/share/snapkit/`, or
`$SNAP_USER_COMMON/` inside the snap, or wherever `SNAPKIT_HOME` points:

```
snaps/btop.json        what it is, where it came from, what to fetch
recipes/btop.yaml      the snapcraft.yaml, as text
icons/btop.svg         lifted out of the payload when it was made
```

It began as one JSON file holding all of it, which is the obvious thing and
does not last. Recipes are most of the weight — at sixteen snaps they were
78% of the file — so every record carried several kilobytes almost nothing
reads, and a change to any field rewrote the lot. At a thousand snaps that is
5.5 MB reparsed by every command and rewritten by every `add`, which a create
does three times: about 130 ms of JSON per create, before any work.

Split up, at a thousand snaps:

| | one file | a directory |
| --- | --- | --- |
| read the register | 14.8 ms | 17.5 ms |
| change one snap | 27.7 ms | 0.1 ms |
| on disk, read to list | 5.5 MB | 0.95 MB |

Reading costs slightly more — a thousand small opens rather than one big
parse — and changing one snap no longer costs anything at all, because it
writes one small file however many there are. The recipe is read only when
something asks for it, so listing a thousand snaps reads none of them.

Each piece stays a thing you can open: the record is legible JSON, the recipe
is a yaml file your editor already understands. A register from before this
changed is migrated on first use, and the old file is kept beside it as
`snapkit.json.migrated` rather than deleted.

Because it is meant to be edited, a record that cannot be read is set aside
rather than taken as the end of the register — one typo costs that record,
not the other nine hundred and ninety nine — and every command says which
one it was until it is fixed.

The register holds everything needed to rebuild:

- a project directory can be deleted and written out again from it
- paste a repository you have already used and it says so, rather than making
  a second copy of it
- removing a snap removes its record, its recipe and its icon

Two repositories whose names collapse to the same snap name -- and there are
a great many repositories called `bat` -- are refused rather than one
replacing the other. Pass `--name` to keep both; the dashboard picks a free
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

A project registered this way keeps its own build. Eleven of the twenty-one
assemble their tree themselves rather than leaving it to snapcraft -- a
core24 snap needs an LXD or Multipass backend, and a project whose whole
content is an upstream binary being restaged does not need a build container
to begin with. Their records name a `pack.py`, and `snapkit build` imports it
and calls its `build(project)` with a `Build` to assemble into:

```python
def build(project):                       # floorp-snap/pack.py
    tarball = project.artifact("floorp-linux-x86_64.tar.xz")
    prime = project.fresh_prime("usr/lib")
    project.run("tar", "xf", tarball, "-C", prime / "usr/lib")
    project.copy_overlay("meta/snap.yaml")
    project.gnome_helpers()
    return project.pack()
```

The dependency runs this way round on purpose. These scripts used to reach
the shared half of themselves along a relative path, which stops working the
moment the tool is installed as a snap, and meant every project spent four
lines on `sys.path` before it could say anything about itself. A `pack.py`
imports nothing: everything it can use is on the `project` it is handed,
including `say`, `download` and a `module()` for a build that runs to more
than one file.

Six of them are not a GitHub release at all -- Discord's download redirect,
Emacs on ftp.gnu.org, ffmpeg.org, and the apt repositories Signal, Sublime
Text and Unity publish through -- and two more build from the archive GitHub
rolls out of a tag rather than from anything attached to the release. Those
are shapes rather than exceptions, and the shape is in the record:

```json
"upstream": {"kind": "apt", "base": "https://updates.signal.org/desktop/apt",
             "package": "signal-desktop", "index": "..."}
```

`sources.py` holds five -- `apt`, `index`, `redirect`, `tag-archive`, and
`local` for a snap made from a file, which watches the folder that file is in
-- and a record with no `upstream` is an ordinary GitHub release. So every
one of them is checked against its real upstream rather than reported as `not
tracked upstream`:

```
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

```
snapkit search monitor         find it by name, repository or summary
snapkit package btop           build it from the register
```

`package` takes whatever you have: the name, the repository, a pasted URL, or
a word from the summary. Delete the project directory and `package` writes it
back out, `snapcraft.yaml` and icon and all.

Giving `create` a repository it already knows does the same thing rather than
telling you off:

```
$ snapkit create https://github.com/aristocratos/btop
    aristocratos/btop is already registered as btop (1.4.7) -- building it
      from the register
    (--name makes a second one; `snapkit update btop` looks for a newer release)
```

## Updating

`snapkit check` asks every registered repository what it has now. `snapkit
update <name>` moves that snap onto it and rebuilds.

Upstreams rename their assets -- btop's musl build went from `.tbz` to
`.tar.gz` in a patch release. When the stored pattern stops matching, the
best asset of the same kind is taken instead and you are told that it
happened, rather than the update failing or quietly packaging something else.

There are two things a project can mean by "the source", and an update has to
know which. Most recipes name a URL and let snapcraft fetch it at build time:
an update repoints that line, rewrites its checksum, and leaves every other
edit in the file alone. The rest open a file that has to be sitting in the
project directory before the build can start, so an update downloads it
there, drops the superseded one, and replaces the version wherever the
project spells it out -- the recipe, `overlay/meta/snap.yaml`, the README.
Every line it rewrites is printed, because a bump should be reviewable
without diffing afterwards, and that matters most when a version string turns
up somewhere nobody expected it.

Where upstream publishes no checksum, what the download is checked against is
also in the record: a detached GPG signature for Emacs and ffmpeg, and for
the tag archives a file that has to be inside the tarball -- a tag that does
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

## Commands

```
snapkit                          the dashboard
snapkit create <repo>            make a snap from a repository
snapkit create ./thing.deb       ... or from a file you already have
snapkit create ~/Downloads       ... or from whichever of those is in there
snapkit create                   ... and with nothing named, it asks
snapkit package <name|repo>      build one already registered, from the register
snapkit search <text>            find one by name, repository or summary
snapkit list                     what is registered
snapkit show <name>              one record, in full, including the recipe
snapkit check [name ...]         what has a newer release upstream
snapkit update <name> [...]      move onto that release and rebuild
snapkit update <name> --force    redo it even if it is already current
snapkit build <name>             hand the project to snapcraft
snapkit remove <name>            forget a snap, and its recipe with it
```

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

```
./tests.py            everything that needs no network
./tests.py --online   those, and the ones that talk to GitHub
```

No framework and no dependencies beyond the tool's own, so it runs anywhere
the tool does. The offline tests build their own `.deb` rather than
downloading one, so the archive reader is checked against bytes the test file
made and knows the shape of.

One function per subject -- upstreams, recipes, register, payloads, projects,
checking, dashboard, updater, from_a_file -- so a failure names the area
before it names the case.

Several exist because of bugs that were in here:

- a build's output was read as text, and Python's universal newlines split on
  the `\r` a progress bar redraws itself with -- so every frame of every bar
  became its own line in the log pane and buried everything else
- cancelling a build raised out of the reading loop, and `Popen.__exit__`
  closes the pipe and *waits*: stopping a ten-minute build took ten minutes.
  (The first version of that test raised `KeyboardInterrupt`, which
  `Popen.__exit__` special-cases into giving up on its own -- so it passed
  whether or not the child was killed. It raises an ordinary exception now.)

- an asset name split on its separators turned `x86_64` into `x86` and `64`,
  so every 64-bit build read as 32-bit and lost to whatever named no
  architecture at all -- on btop's release page, m68k
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
- `select()` watches a file descriptor and `sys.stdin` is buffered, so
  reading one character of an arrow key pulled the rest into Python's buffer
  where `select` could not see it; the sequence read as a lone Escape, and
  Escape quit -- so every arrow key closed the dashboard

## Building this

```
./build.py
sudo snap install --dangerous --classic snapkit_0.1.0_amd64.snap
```

It is a classic snap because building a snap means running snapcraft and
writing project directories wherever you keep them, and a confined snap can
do neither.

## Caveats

- The register scales; the *projects* do not, and that is the thing to watch.
  A thousand registered snaps is a megabyte. A thousand built snaps is
  however large those snaps are — Electron apps run to a hundred megabytes
  each — sitting in `projects/`. Nothing here prunes them.

- amd64 only, which is what the classifier looks for.
- `grade: devel` on the generated recipes' sibling -- this tool is new.
- A snap built from someone else's release is not published or endorsed by
  them, and every recipe it writes says so in its description.
