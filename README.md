<!-- markdownlint-disable MD033 -->
# snapkit

[![tests](https://github.com/bearenbey/snapkit/actions/workflows/tests.yml/badge.svg)](https://github.com/bearenbey/snapkit/actions/workflows/tests.yml)
[![licence](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![base](https://img.shields.io/badge/snap%20base-core24-informational)](snap/snapcraft.yaml)

snapkit makes a snap package out of a GitHub release, or out of a `.deb`,
archive or AppImage you already have, and keeps it in step with upstream
afterwards.

It reads the release rather than trusting its file names: it downloads what
the project published, opens it to find where the program and its desktop
entry and icon really are, works out which libraries the snap has to stage,
and writes a `snapcraft.yaml` around what it found.

Homepage: <https://github.com/bearenbey/snapkit>

> [!IMPORTANT]
> **This project is co-authored with Claude, Anthropic's Opus 5 model,
> which wrote roughly a quarter of the source; the design, the register
> and the update model, and the packaging decisions behind every snap
> came first and are not its work.**

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

## Features

- **Reads the payload, does not guess at it.** The release is downloaded and
  opened. Where the binary, the `.desktop` entry and the icon actually live is
  read out of it, not inferred from the file name.

- **Works out what to stage.** `DT_NEEDED` is read straight out of the ELF, a
  `.deb`'s own `Depends:` is taken into account, and what the base and the
  gnome extension already supply is subtracted. A library it cannot trace to a
  package is reported rather than guessed at.

- **Knows what it cannot package.** A source tarball is refused with the build
  system it found instead of being copied into a snap full of source, and a
  release with nothing usable attached says what it does publish.

- **Keeps snaps in step with upstream.** GitHub releases, an apt repository, a
  listing of every release, a download endpoint that redirects, a bare tag, or
  a folder on your disk. `snapkit check` says what has moved; `snapkit update`
  moves onto it and rebuilds.

- **A register, not a pile of directories.** One JSON record per snap with the
  recipe beside it, so a project directory can be thrown away and written out
  again, and removing a snap removes the whole of it.

- **A shared recipe database.** Projects packaged on one machine can be built
  on another over plain https, with no token, no login and no git.

- **A dashboard, or a command line.** Run it with no arguments for a full
  screen view of everything registered, or drive every part of it from
  scripts and cron.

- **Small and stdlib-only.** One dependency, `rich`, and only for the
  dashboard. Everything else is the Python standard library.

## Installation

snapkit needs Python 3.10 or newer and `snapcraft` to do the building.

```sh
git clone https://github.com/bearenbey/snapkit
cd snapkit
./snapkit.py --help
```

It can also be built as a snap of its own, which is classic confinement
because building snaps and writing project directories is not something a
confined snap can do:

```sh
./build.py
sudo snap install --dangerous --classic snapkit_*.snap
```

See [docs/development.md](docs/development.md) for the details.

## Usage

```sh
snapkit                          the dashboard
snapkit create <repo>            make a snap from a repository
snapkit create ./thing.deb       ... or from a file you already have
snapkit check                    what has a newer release upstream
snapkit update <name>            move onto that release and rebuild
snapkit list                     what is registered
snapkit install <name>           fetch it from the database, build, install
```

Every command, with what it takes, is in
[docs/commands.md](docs/commands.md).

## Documentation

| | |
| --- | --- |
| [The dashboard](docs/dashboard.md) | one screen for everything, and what the keys do |
| [How a release becomes a snap](docs/packaging.md) | what it opens, what it can package, what it stages |
| [Where releases are looked for](docs/upstreams.md) | apt, a listing, a redirect, a bare tag, a folder |
| [The shared recipe database](docs/database.md) | recipes published for another machine to build |
| [The register](docs/register.md) | where records live, and importing packaging you have |
| [What this trusts](docs/security.md) | what runs code, and what a checksum does not promise |
| [Every command](docs/commands.md) | the full reference |
| [Tests, and building this](docs/development.md) | how it is checked, and how to build the snap |

## Tests

No network, no snapcraft, no fixtures downloaded at test time.

```sh
python3 tests.py
```

## Copyright

Copyright (c) 2026 bearenbey

Released under the MIT licence. See [LICENSE](LICENSE).

A snap built from someone else's release is not published or endorsed by
them, and every recipe snapkit writes says so in its description.
