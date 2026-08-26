# emacs-snap

A [snap](https://snapcraft.io/) package of [GNU Emacs](https://www.gnu.org/software/emacs/),
the extensible, customizable text editor.

Built from the GNU release tarball against `core24`, in **strict**
confinement, as a **PGTK** (pure GTK) build — a native Wayland client that
falls back to GDK's X11 backend on an X11 session. Tree-sitter, GnuTLS,
SQLite, libxml2, dynamic modules, RSVG/WebP/PNG/JPEG/GIF/TIFF images and
Cairo/HarfBuzz text rendering are enabled.

## Build

Requires `snapcraft` and a build backend (LXD is used by default):

```sh
snapkit build emacs
```

`snapkit build emacs` hands the recipe to `snapcraft pack` as it stands.
Moving it onto a newer release is the other command: `snapkit update emacs`
repoints the `source:` line at the new release, rewrites its checksum, and
builds the result.

Emacs is compiled from source, so a cold build is roughly ten minutes.

This produces `emacs_<version>_<arch>.snap`.

## Install

The snap is not signed by the store, so a local build needs `--dangerous`:

```sh
sudo snap install --dangerous ./emacs_*.snap
```

Then run it as `emacs`, or `emacs -nw` for the terminal interface.

## Apps

| Command | What it is |
| --- | --- |
| `emacs` | The editor, graphical or `-nw` |
| `emacs.emacsclient` | Connects to a running server (`M-x server-start`) |
| `emacs.etags` | Tag file generator (`--ctags` for the old `ctags` behaviour, which Emacs 31 dropped) |
| `emacs.ebrowse` | C++ class browser |

## Interfaces

| Interface | Auto-connects | What it is for |
| --- | --- | --- |
| `home` | yes | Reading and writing non-hidden files in `$HOME` |
| `network`, `network-bind` | yes | Tramp, package archives, the Emacs server |
| `browser-support` | yes | Opening links in the host browser |
| `audio-playback` | yes | The bell, and `play-sound-file` |
| `cups` | yes | Printing |
| `desktop`, `desktop-legacy`, `wayland`, `x11`, `opengl`, `gsettings` | yes | Added by the `gnome` extension |
| `removable-media` | no | Files under `/media` and `/mnt` |
| `password-manager-service` | no | Secrets via the freedesktop Secret Service (`auth-source`) |
| `mount-observe` | no | Reading `/proc/self/mountinfo`, which `df`-style commands want |

Connect the optional ones with:

```sh
sudo snap connect emacs:removable-media
sudo snap connect emacs:password-manager-service
sudo snap connect emacs:mount-observe
```

## Where the config lives

Strict confinement remaps `$HOME`, so Emacs reads its configuration from:

```
~/snap/emacs/current/.emacs.d/
```

not `~/.emacs.d`. Your real home directory is still readable and writable
through the `home` interface, which deliberately does **not** grant access to
dot-directories at the top of it — so an existing `~/.emacs.d` is out of
reach. Copy it across, or symlink individual files from inside the snap's
home to non-hidden paths in your real one.

## Why PGTK and not the X11 toolkit

This is the one thing about this recipe that is not a preference, and it is
worth writing down because the failure looks like a confinement problem and
is not one.

An Emacs configured `--with-x-toolkit=gtk3` is an X11-only client, and says so
to GDK by calling `gdk_set_allowed_backends("x11")`. The `gnome` extension's
launcher does the opposite: `/snap/gnome-46-2404/current/command-chain/desktop-launch`
exports `GDK_BACKEND=wayland` whenever a Wayland session is available. The
allowed set and the requested backend do not intersect, GDK opens nothing, and
GTK reports:

```
(emacs:NNNN): Gtk-WARNING **: cannot open display: :0
```

That message points at X11 and at confinement, and both are red herrings —
the X socket, the `XAUTHORITY` cookie and the auth entries are identical
inside the snap and on the host, and the profile logs no AppArmor denials at
all. Building `--with-pgtk` makes Emacs a GDK client that can use either
backend, which is what the launcher's environment requires. It is also the
better build on a Wayland desktop: native rendering and fractional scaling
rather than XWayland.

If you ever do want the X11 toolkit build back, it needs `GDK_BACKEND: x11`
in the app's `environment:` block to override the launcher.

**The PGTK caveat**, which the binary itself will tell you: *"Due to a
limitation in GTK 3, Emacs built with PGTK will simply exit when the display
connection is closed."* If the compositor connection drops, Emacs dies rather
than surviving to save. Use the server and `emacsclient`, and save often.

## Why the prefix is `/snap/emacs/current/usr`

Emacs bakes the absolute paths of its Lisp, `etc` and `libexec` trees into the
binary at configure time and does not relocate at runtime. Configuring with
`--prefix=/usr` would produce a binary that looks for its Lisp in the host's
`/usr/share/emacs`, which inside a snap is the base snap and does not have it.

So the recipe configures with `--prefix=/snap/emacs/current/usr` — the path
the snap is actually mounted on — and the part's `organize:` moves the
installed tree from `snap/emacs/current/usr` back to `usr` so it lands in the
right place in the package. The compiled-in paths then resolve at runtime.

## Native compilation

Disabled. Enabling it (`--with-native-compilation`) means shipping `gcc`,
`libgccjit` and `binutils` inside the snap and wiring up the paths so
ahead-of-time and just-in-time compilation can find them at runtime. Emacs is
byte-compiled here instead, which is how it ran for decades.

## Updating to a new Emacs release

```sh
`snapkit update emacs`            # or: `snapkit update emacs` --build
```

This is an `artifact` project: `snapkit update emacs` resolves the newest tarball from
the [GNU release index](https://ftp.gnu.org/gnu/emacs/), downloads it into
this directory, checks its detached GPG signature, rewrites every mention of
the old version, and removes the superseded tarball.

GNU publishes no checksum file for Emacs, so the signature is the only thing
that says the download is what GNU released. Verification needs the release
key in your keyring, which it is not by default — until then the update
reports `gpg: NOT verified (release key not in your keyring)` and continues.
To make it a real check:

```sh
curl -sO https://ftp.gnu.org/gnu/gnu-keyring.gpg
gpg --import gnu-keyring.gpg
```

Emacs releases are currently signed by Eli Zaretskii, key
`17E9 0D52 1672 C046 31B1 183E E78D AE0F 3115 E06B`.

## Layout

```
snap/snapcraft.yaml   the package definition
snap/gui/emacs.png    the icon, extracted from the tarball
emacs-<version>.tar.xz  the upstream tarball, managed by snapkit
```
