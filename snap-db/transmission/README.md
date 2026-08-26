# Transmission snap (built from the official source release)

Compiles `transmission-4.1.3.tar.xz` into a strictly confined snap
(`transmission_4.1.3_amd64.snap`) containing the GTK 4 client, the daemon,
the web UI and the command-line utilities.

Unlike the other snaps in this tree this one is a real build, not a repack.
snapcraft compiles it in an Ubuntu 24.04 (noble) instance, so the binaries are
linked against the same glibc as the `core24` snap base. Two parts: gtkmm
4.12, which Transmission 4.1 needs and noble does not carry, and Transmission
itself built against it.

Until 2026-08-25 this project carried its own Ubuntu 24.04 build root,
assembled from plain `.deb` files by `tools/fetch-buildroot.py` and entered
with `bubblewrap`, because the machine it was written on had no build backend.
That was about a gigabyte of checked-out chroot; snapcraft does the same job.

## Build

```sh
snapkit build transmission
```

`snapkit build transmission` packs whatever the project points at now. Moving it
onto a newer release is the other command: `snapkit update transmission` fetches the
tarball, drops the superseded one, rewrites the version wherever this project
spells it out, and builds the result.

## Install / run

```sh
sudo snap install --dangerous transmission_4.1.3_amd64.snap
transmission                       # GTK client
transmission.daemon -f             # daemon in the foreground, web UI on :9091
transmission.remote -l             # list torrents
transmission.create / .edit / .show / .cli
```

`--dangerous` is required because the snap is not signed by the store. To
remove: `sudo snap remove transmission`.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: metadata, apps, plugs, and the parts snapcraft builds |
| `pack.py` | check the tarball's version, then run snapcraft |
| `overlay/meta/gui/transmission.desktop` | desktop entry snapd exports to the host menu |
| `overlay/bin/launcher` | GTK client entry point; fixes the gtkmm search order |
| `vendor/gtkmm-4.12.0.tar.xz` | see below |

`snap/command-chain/desktop-launch` and `hooks-configure-fonts` come from
snapcraft's `gnome` extension, which the recipe asks for by name. They used to
be copied out of the installed `gnome-46-2404` snap at build time.

## Design notes

- **base `core24` + strict confinement.** GTK 4, glib, glibmm/giomm, pangomm,
  cairomm, libsigc++, libcurl and the fonts come from `gnome-46-2404` at
  `$SNAP/gnome-platform`; OpenSSL comes from the base snap; graphics come from
  `mesa-2404` at `$SNAP/gpu-2404`.
- **gtkmm 4.12 is built and shipped.** Transmission 4.1 requires
  gtkmm ≥ 4.11.1 and noble — hence both the build root *and* the
  `gnome-46-2404` runtime platform — only carries 4.10. (That version gap is
  why Ubuntu 24.04 still packages Transmission 4.0.) Everything gtkmm 4.12
  itself needs is already in noble, so `pack.py` builds just that one
  library with meson and stages it in the snap. Its soname is identical to
  the platform's 4.10, so `bin/launcher` prepends `$SNAP/usr/lib/$ARCH` to
  `LD_LIBRARY_PATH` after `desktop-launch` has run, making ours win.
- **Vendored third-party code is used as shipped.** The release tarball
  bundles libevent, libpsl, libdeflate, miniupnpc, libnatpmp, dht, libutp,
  fmt, rapidjson and friends, and `USE_SYSTEM_*` is forced `OFF` so the build
  is reproducible from the tarball alone. Only OpenSSL, libcurl and zlib come
  from the distribution.
- **The web UI ships prebuilt** (`INSTALL_WEB=ON`, `REBUILD_WEB=OFF`), so no
  Node.js is needed. `TRANSMISSION_WEB_HOME` points the daemon at it.
- **No systemd integration** (`WITH_SYSTEMD=OFF`) and the daemon is a plain
  app, not a snap `daemon:` service — nothing starts in the background on
  install. Run `transmission.daemon -f` yourself if you want it.
- **Interfaces granted:** the GTK client gets desktop, desktop-legacy,
  gsettings, opengl, wayland, x11, unity7, network, network-bind, home,
  removable-media, screen-inhibit-control; the CLI tools get network,
  network-bind, home, removable-media.
- Torrents can only be read from and written to `$HOME` (excluding dotfiles,
  which the `home` interface does not cover) and removable media. Config
  lives in `~/snap/transmission/current/.config/transmission/`; copy an
  existing `~/.config/transmission` there after the first launch to carry it
  over.

## Publishing to the store

This build is meant for local installs. The snap name would have to be
registered (and available) before anything could be uploaded, and a store
build would want the whole thing expressed as a `snapcraft.yaml` instead.
