# RetroArch snap, built from the Flatpak recipe

This directory packages RetroArch as a snap using the Flathub recipe for
`org.libretro.RetroArch` as the source of truth. The starting point was
`org.libretro.RetroArch.flatpakref`, which is a pointer to the Flathub app
rather than a bundle, so the packaging was reconstructed from the upstream
manifest at [flathub/org.libretro.RetroArch](https://github.com/flathub/org.libretro.RetroArch).

The set of components in `snap/snapcraft.yaml`, and the build flags, are copied
from that manifest, so this snap builds the same tree of code and data the
Flatpak ships. The *revisions* are not copied -- see "What this tracks" below.

## Build

```
snapkit update retroarch   # onto the newest release, and build it
sudo snap install ./retroarch_1.22.2_amd64.snap --dangerous
sudo snap connect retroarch:joystick
sudo snap connect retroarch:raw-usb
```

The `joystick`, `raw-usb`, `bluez`, `camera`, `optical-drive`,
`hardware-observe`, `mount-observe`, `system-observe`, `audio-record` and
`removable-media` interfaces are not auto-connected for a locally installed
snap, so connect the ones you need. `snap connections retroarch` lists
the current state.

Graphics userspace comes from the `mesa-2404` content snap, which snapd pulls in
automatically on install.

## What this tracks

This project is registered in `../snapkit/projects.py` as a `yaml-source`
project, so `snapkit update` bumps it like any other:

```
snapkit check retroarch        # is retroarch behind?
snapkit update retroarch       # bump to the newest release
snapkit build retroarch        # build what the recipe points at now
```

That has a cost worth stating plainly. The Flathub manifest pins every module
to an exact commit, and the first version of this recipe did the same. To fit
the shared updater, RetroArch is now built from the upstream release tarball
(the tag archive, checksummed in the yaml) and the seven data repositories
follow their default branches instead. So:

- the frontend is reproducible and verified -- `source-checksum` is checked by
  snapcraft at build time, and the updater refuses a tarball that does not
  contain `RetroArch-<version>/version.all`
- the bundled assets, database, core info, autoconfigs, shaders and overlays
  are **not** pinned; two builds of the same RetroArch version can bundle
  different data. Upstream does not version those repositories, which is why
  the Flatpak pins commits and the official snap tracks `master`
- this snap therefore tracks RetroArch releases, not the Flatpak. It no longer
  reproduces a specific Flatpak build

Restore the old behaviour by putting `source-commit:` back on each part and
dropping the registry entry; the commits the Flatpak used are in the manifest.

## Module mapping

| Flathub module | snapcraft part |
| --- | --- |
| `retroarch` | `retroarch` |
| `retroarch-filters-video` | `retroarch-filters-video` |
| `retroarch-filters-audio` | `retroarch-filters-audio` |
| `retroarch-assets` | `retroarch-assets` |
| `libretro-database` | `libretro-database` |
| `libretro-core-info` | `libretro-core-info` |
| `retroarch-joypad-autoconfig` | `retroarch-joypad-autoconfig` |
| `slang-shaders` | `slang-shaders` |
| `glsl-shaders` | `glsl-shaders` |
| `common-overlays` | `common-overlays` |

The Flatpak's build flags are carried over verbatim: `--enable-dbus` for
configure, and `HAVE_TRANSLATE=1 HAVE_ACCESSIBILITY=1 HAVE_UPDATE_ASSETS=0
HAVE_UPDATE_CORE_INFO=0` for make. The last two disable downloading assets and
core info through the Online Updater, since both are bundled read-only; core
downloading itself stays enabled and writes to the user's config directory.

`snap/local/retroarch.cfg` is the Flatpak's `retroarch.cfg` with `@prefix@`
resolved to `/usr`. RetroArch installs it as the system-wide skeleton config
(`GLOBAL_CONFIG_DIR`) and copies it into the user's config directory on first
run only, so everything in it stays user-editable afterwards.

## Deliberate differences from the Flatpak

- **No Cg shader support, and `common-shaders` is not bundled.** The Flatpak
  builds against the NVIDIA Cg toolkit, a discontinued proprietary binary blob,
  purely so the Cg presets in `common-shaders` can load. The snap leaves
  `--enable-cg` off (configure defaults it to `auto`, and no `libCg` is
  present), and correspondingly omits `common-shaders`. The GLSL and slang
  shader collections, which is what current RetroArch actually uses, are both
  bundled. The Flatpak also marks `common-shaders` `x86_64`-only, so it was
  never available on arm64 there either.
- **No gamescope WSI layer.** The Flatpak ships a Vulkan layer for running under
  gamescope, which has no equivalent inside strict confinement.
- **No Discord / gamescope IPC socket access.** The Flatpak pokes holes for
  `xdg-run/discord-ipc-0` and `xdg-run/gamescope-0`; snap interfaces have no
  counterpart, so Discord rich presence will not connect. RetroArch is still
  built with `--enable-discord` so the feature is compiled in.
- **`--filesystem=host` becomes `home` + `removable-media`.** Strict confinement
  has no equivalent of full host filesystem access. Content outside `$HOME` and
  removable media is not reachable.

## Snap-specific plumbing

These have no Flatpak counterpart; they exist because a strictly confined snap
gets a remapped `$HOME` and relocated libraries.

- `layout:` bind-mounts `$SNAP/usr/share/libretro` and `$SNAP/usr/lib/retroarch`
  onto the absolute paths RetroArch bakes in at compile time
  (`ASSETS_DIR`, `FILTERS_DIR`, `CORE_INFO_DIR`), and `$SNAP/etc/retroarch.cfg`
  onto `/etc/retroarch.cfg`. This is what makes the Flatpak's prefix-relative
  config work unchanged, and keeps those paths stable across snap revisions.
- `snap/local/launcher` points the content browser at `$SNAP_REAL_HOME` on first
  run and exports `PULSE_SERVER` for the host PulseAudio socket.
- `ESPEAK_DATA_PATH` is set because the accessibility feature
  (`HAVE_ACCESSIBILITY=1`) `exec()`s a binary literally named `espeak`;
  `espeak-ng-espeak` is staged to provide it.
- `LIBDECOR_PLUGIN_DIR` is set so Wayland client-side decorations load.
- `snap/local/launcher` symlinks the compositor's Wayland socket into the snap's
  private `$XDG_RUNTIME_DIR`. snapd gives the snap a runtime dir of its own
  (`/run/user/<uid>/snap.retroarch`), but the socket lives one level up, and
  libwayland resolves `$WAYLAND_DISPLAY` relative to `$XDG_RUNTIME_DIR`. Without
  this, RetroArch fails with "Failed to create wl_display", Qt cannot load its
  wayland platform plugin, and the app silently falls back to XWayland.
- `/usr/share/X11/xkb` is bound in and `XKB_CONFIG_ROOT` is set, with `xkb-data`
  added to `stage-packages`. `libxkbcommon0` only *recommends* `xkb-data`, so
  nothing pulled it in, and neither core24 nor mesa-2404 provides it. Without the
  keymap data, Qt's wayland plugin fails with "failed to create xkb context" and
  then segfaults dereferencing the null context. This only surfaces once Wayland
  actually works; under XWayland fallback it stays hidden.
- `/usr/share/alsa` is bound into the sandbox for the same reason as the libretro
  directories: ALSA opens `/usr/share/alsa/alsa.conf` by absolute path and the
  core24 base does not ship it, so the `alsa` audio driver would otherwise fail
  with "Cannot access file /usr/share/alsa/alsa.conf".
- `input_joypad_driver = "sdl2"` is set in the skeleton config, because the
  `udev` driver needs raw `/dev/input` access that only works once the
  `joystick` interface is connected.

## Interfaces worth connecting

`sudo snap connect retroarch:alsa` — without it the ALSA sequencer is denied and
you get `open /dev/snd/seq failed: Operation not permitted`. That only affects
MIDI; ordinary audio goes through PulseAudio and works unconnected.

## Known harmless messages

`update.go:193: cannot change mount namespace according to change mount
(/var/lib/snapd/hostfs/boot /boot none bind,ro 0 0): permission denied` comes
from snapd, not from this package. snapd bind-mounts the host's `/boot` into
every snap's mount namespace and this fails on some systems. Other snaps print
the same line, and nothing in `snapcraft.yaml` affects it.

## Snap name

The snap is named `retroarch`. Note that `retroarch` is also the name of the
libretro team's own snap in the Snap Store, so the two cannot be installed side
by side, and this name cannot be registered for publishing. Locally, install
with `--dangerous` as above; if the store snap is already installed, remove it
first.
