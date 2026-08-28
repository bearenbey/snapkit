# irssi-snap

A [snap](https://snapcraft.io/) package of [irssi](https://irssi.org/), the
modular text mode IRC client. This snap is not published or endorsed by the
upstream project.

Built from the upstream release tarball against `core24`, in **strict**
confinement, with Perl scripting, the IRC proxy module, OTR, true colour and
utf8proc enabled.

## Build

Requires `snapcraft` and a build backend (LXD is used by default):

```sh
snapkit build irssi
```

`snapkit build irssi` hands the recipe to `snapcraft pack` as it stands.
Moving it onto a newer release is the other command: `snapkit update irssi`
repoints the `source:` line at the new release, rewrites its checksum, and
builds the result.

This produces `irssi_<version>_<arch>.snap`.

## Install

The snap is not signed by the store, so a local build needs `--dangerous`:

```sh
sudo snap install --dangerous ./irssi_*.snap
```

Then run it as `irssi`.

## Interfaces

| Interface | Auto-connects | What it is for |
| --- | --- | --- |
| `network`, `network-bind` | yes | Connecting to IRC servers; the built-in proxy listening locally |
| `home` | yes | Reading and writing non-hidden files in `$HOME` (`/upload`, `/dcc get`, log paths) |
| `removable-media` | no | Same, for `/media` and `/mnt` |
| `dot-irssi` | no | Opt-in access to your real `~/.irssi` |

Connect the optional ones with:

```sh
sudo snap connect irssi:removable-media
sudo snap connect irssi:dot-irssi
```

## Where the config lives

By default irssi's `$HOME` inside the snap is `~/snap/irssi/current`, so the
config, logs and scripts live in:

```
~/snap/irssi/current/.irssi/
```

The `home` interface deliberately does **not** grant access to dot-directories
at the top of your home directory, so `~/.irssi` is unreachable until you opt
in. If you want the snap to use your existing config instead:

```sh
sudo snap connect irssi:dot-irssi
```

The launcher detects that connection and starts irssi with
`--home=$HOME/.irssi`. Passing `--home` yourself always wins over both.

## Perl scripts

Perl support is compiled in and the Perl runtime is bundled. Drop scripts in
`<irssi home>/scripts/` and `/script load` them as usual. Scripts that shell
out to other programs or pull in CPAN modules from the host will not work.
only what is inside the snap is visible.

## Terminals

The bundled `ncurses-term` database plus the entries in the base snap cover
essentially every common `TERM`, including `alacritty`, `foot`, `wezterm`,
`st-256color` and `vte-256color`.

Terminals that ship their own terminfo rather than getting it from ncurses,
kitty (`xterm-kitty`) and ghostty, are the exception: their entries live in
`~/.terminfo`, which strict confinement puts out of reach. Set
`TERM=xterm-256color` for the irssi window if you use one of those.

## Layout

```
snap/snapcraft.yaml     the package definition
snap/local/irssi-launch launcher: terminfo, PERL5LIB, config dir selection
```

### Why the `layout:` section matters

irssi bakes its module and data directories in as absolute paths
(`/usr/lib/irssi/modules`, `/usr/share/irssi`) and offers no environment
variable to redirect them. Inside a snap those paths do not exist, and irssi
does not treat that as fatal. It starts up looking perfectly healthy while
Perl scripting, OTR, the IRC proxy, `/help` and the bundled themes are all
quietly missing.

The `layout:` binds in `snapcraft.yaml` map both directories into `$SNAP`,
and `--libdir=lib` keeps the module path free of the architecture triplet so
a single layout works on every architecture. If you change either, re-check
that `/script list` and `/load otr` still work, because a broken build looks
identical to a working one until you ask for those.

## Updating to a new irssi release

In `snap/snapcraft.yaml`, bump the `source:` URL and replace
`source-checksum:` with the new tarball's sha256:

```sh
curl -sLO https://github.com/irssi/irssi/releases/download/<ver>/irssi-<ver>.tar.xz
sha256sum irssi-<ver>.tar.xz
```

The snap `version` is read from the source's `meson.build`, so it follows
automatically.
