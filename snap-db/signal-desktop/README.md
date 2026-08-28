# Signal Desktop snap (repack of the official .deb)

Builds a strictly confined `signal-desktop` snap from the `.deb` that Signal
Messenger publishes for Debian/Ubuntu. This snap is not published or endorsed
by the upstream project.

Signal's download page only offers the apt repository, never a direct `.deb`
link, which is why the package looks like it does not exist. It does, and the
repo's pool is a plain HTTP directory:

```
https://updates.signal.org/desktop/apt/pool/s/signal-desktop/signal-desktop_8.23.0_amd64.deb
```

`snap/snapcraft.yaml` points straight at that URL with the SHA256 taken from
the repo's own `Packages` index, so nothing has to be downloaded by hand.

## Build

```sh
snapkit build signal-desktop
```

`snapkit build signal-desktop` hands the recipe to `snapcraft pack` as it stands.
Moving it onto a newer release is the other command: `snapkit update signal-desktop`
repoints the `source:` line at the new release, rewrites its checksum, and
builds the result.

Needs `snapcraft` and LXD. The `.deb` (~120 MB) is fetched during the build
and verified against `source-checksum`. Output is
`signal-desktop_8.23.0_amd64.snap`, ~137 MB.

## Install / run

```sh
sudo snap install --dangerous signal-desktop_8.23.0_amd64.snap

# not auto-connected on a locally built snap:
sudo snap connect signal-desktop:password-manager-service
sudo snap connect signal-desktop:camera
sudo snap connect signal-desktop:audio-record

signal-desktop
```

`--dangerous` is required because the snap is not signed by the store. To
remove it again: `sudo snap remove signal-desktop`.

Link it to your phone the usual way. Signal Desktop is a linked device, not a
standalone account.

## Updating

```sh
`snapkit update signal-desktop`            # newest stable in Signal's apt repo
`snapkit update signal-desktop` 8.22.0     # or pin a version
snapcraft pack
```

The script reads the repo's `Packages` index, rewrites `version:`, `source:`
and `source-checksum:` in `snapcraft.yaml`, and stops early if you are already
on the newest release.

This is the only way this snap moves forward. Signal Desktop only ships a
self-updater for macOS and AppImage builds. On a `.deb`-style install its
updater is never even constructed, so nothing in the app will try (or manage)
to replace the read-only squashfs underneath it.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: deb source, interfaces, staged libraries |
| `snap/local/signal-launch` | app entry point, picks the Ozone platform and sandbox mode |
| `snap/gui/signal-desktop.png` | icon snapd shows for the snap itself |

## Design notes

- **base `core24`, `gnome` + `gpu` extensions.** GTK, GLib, NSS, fontconfig
  and pulse come from the `gnome-46-2404` content snap; the entire GL/VAAPI
  stack *and* libX11/libxcb come from `mesa-2404` via `$SNAP/gpu-2404`.
  Neither core24 nor the GNOME platform snap ships the X client libraries, so
  the `gpu` extension is load-bearing here, not just a graphics nicety.
- **`cleanup` part.** Everything the base or the GNOME platform snap already
  provides is deleted from the prime tree, so GTK and friends get loaded once,
  from the platform, at the version its module and loader caches expect. It
  matches on path first and then again on soname, because the platform ships
  different point releases (`libgtk-3.so.0.2418.32` against the staged
  `libgtk-3.so.0.2409.32`), so a path-only pass takes the soname symlink and
  leaves ~25 MB of unreachable real files behind.
- **Keyring.** Signal encrypts its SQLCipher database key with Electron's
  `safeStorage`, which goes through libsecret to `org.freedesktop.secrets` over
  D-Bus. Without `password-manager-service` connected Signal falls back to
  keeping that key in plaintext beside the database, so connect it before you
  link the device.
- **Wayland.** The launcher passes `--ozone-platform-hint=auto`, so it runs
  natively on a Wayland session and on X11 otherwise. Set `SIGNAL_FORCE_X11=1`
  to stay on XWayland if decorations or screen sharing misbehave.
- **Interfaces granted:** network, network-bind, browser-support,
  audio-playback, audio-record, camera, home, removable-media,
  screen-inhibit-control, password-manager-service, hardware-observe, unity7,
  plus desktop, desktop-legacy, gsettings, opengl, wayland and x11 from the
  `gnome` extension.
- **Data lives in `~/snap/signal-desktop/current/.config/Signal`**, not
  `~/.config/Signal`. An existing desktop install can be carried over by
  copying that directory in after the first launch (with Signal closed).
- **The polkit policies** shipped in `resources/` (enable-backups,
  plaintext-export, view-aep) cannot be registered from inside a snap, so the
  features behind those authentication prompts are not expected to work.

## Publishing to the store

This build is meant for local installs. It repacks upstream's prebuilt
binaries rather than building from source, `signal-desktop` would have to be a
registered and available snap name, and redistributing a build that talks to
Signal's servers is something Signal asks you to clear with them first.
