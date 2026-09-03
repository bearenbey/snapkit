# How a release becomes a snap

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

## Source releases


Not every project attaches a built program to its release. tmux publishes
`tmux-3.7c.tar.gz`, which is C source and a `configure` script, and nothing in
it has been compiled. `create` packages what a project already built, so it
refuses those and says which build system it found rather than copying a
source tree into a snap.

What settles it is not the file name and not what looks executable. A source
tree carries its own executable scripts, autotools' `compile` and `install-sh`
and `missing` among them, and picking one of those makes a snap whose command
prints `compile: No command`. It is decided by whether anything in the tree is
compiled at all.

Building from source is a different recipe: `plugin: autotools` or `meson` or
`cmake`, and the `-dev` packages it builds against, which nothing here can
work out on its own. `emacs`, `ffmpeg` and `irssi` in the shared database are
that shape, written by hand and brought in with `snapkit import`.

## Electron, and the sandbox it cannot have


Electron ships `chrome-sandbox`, a helper that has to be owned by root with
mode 4755. No file in a snap can carry a setuid bit, so an Electron app
started from its own binary aborts before it draws anything:

    The SUID sandbox helper binary was found, but is not configured
    correctly. Rather than run without sandboxing I'm aborting now.

Electron has a second sandbox that uses user namespaces and needs no such bit.
So an app that looks like Electron is started through a small wrapper that
keeps that one where the kernel provides it, and gives the sandbox up only
where it does not. Dropping it unconditionally is the usual advice and it
turns the sandbox off on every machine that did not need it off.

The wrapper is written to `snap/local/`, staged into `bin/`, and named as the
app's `command:`. It is written once and left alone, so editing it is safe.

## What it needs at runtime


A prebuilt binary is not self-contained. It links against libraries that were
on the machine that built it, and a snap only has what the base gives it plus
whatever the recipe stages. Getting that list wrong is the difference between
a snap that runs and one that dies on a missing `.so`, so it is worked out
rather than guessed:

- **The binary is asked directly.** `DT_NEEDED` is read out of the ELF header,
  which says what it loads without running it or resolving it against this
  machine. What the payload ships for itself is followed too, so a portable
  build that brings its own Qt is not asked to stage one.
- **A `.deb` is asked as well.** Its `Depends:` is the packager's own answer
  and covers things the binary never names, such as a plugin opened later.
- **What the platform already has is subtracted.** `core24` supplies 287
  libraries and the gnome extension another 1376. Staging those again is
  wasted space at best and a fight with the platform snap at worst, which is
  why no recipe here both uses the extension and stages GTK.
- **noble renamed a hundred packages.** `libasound2` is `libasound2t64` on
  core24, and a recipe that says otherwise stops at "no such package".

Where a library cannot be traced to a package, it is **named in a warning
rather than turned into a guess**. Guessing wrong in that direction produces a
recipe that does not build at all; being one package short produces one that
builds and possibly runs, and says what to add. The naming convention alone
gets it right about 40% of the time, which is why it is not relied on: the
table is built from packages that recipes here already build with.

Two things it cannot see. A library opened by name at runtime appears in no
header and no `Depends:` -- hardware video decoding and tray icons are usually
this. And a driver's own libraries come from the host through an interface, so
`libcuda.so.1` is reported and never staged, because naming a package for it
would pin one driver version.

`tools_platform_gen.py` rebuilds that table from the snaps installed on a
machine, so it can be refreshed when the base or the extension moves.

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

---

[Back to the README](../README.md)
