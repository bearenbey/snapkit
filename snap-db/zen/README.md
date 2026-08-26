# Zen Browser snap (repack of the official tarball)

Turns `zen.linux-x86_64.tar.xz` into a strictly confined snap
(`zen_1.21.15b_amd64.snap`). snapcraft builds it from `snap/snapcraft.yaml`;
`pack.py` adds the checks a recipe cannot express and then runs it.

## Build

```sh
snapkit build zen
```

`snapkit build zen` packs whatever the project points at now. Moving it onto
a newer release is the other command: `snapkit update zen` fetches the
tarball, rewrites the version wherever this project spells it out, and builds
the result.

## Install / run

```sh
sudo snap install --dangerous zen_1.21.15b_amd64.snap
sudo snap connect zen:browser-sandbox            # see below — do this one
snap connect zen:password-manager-service        # optional
snap connect zen:u2f-devices                     # optional, hardware keys
zen
```

`--dangerous` is required because the snap is not signed by the store. To
remove: `sudo snap remove zen`.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: metadata, apps, plugs, and the parts snapcraft builds |
| `pack.py` | run snapcraft, then check the packed snap's version and libraries |
| `overlay/meta/gui/zen.desktop` | desktop entry snapd exports to the host menu |
| `overlay/bin/launcher` | the app entry point |
| `overlay/opt/zen/distribution/policies.json` | Gecko enterprise policies, dropped next to the binary |

The window icon comes out of the tarball at build time
(`browser/chrome/icons/default/default128.png`) rather than being kept here,
and `snap/command-chain/desktop-launch` and `hooks-configure-fonts` are copied
out of the installed `gnome-46-2404` snap — the same files snapcraft's `gnome`
extension pulls from the matching SDK.

## Design notes

- **base `core24` + strict confinement.** Nothing is bundled beyond what
  upstream ships: GTK, GLib, fontconfig, NSS, pulse, cups and the hunspell
  dictionaries come from the `gnome-46-2404` content snap mounted at
  `$SNAP/gnome-platform`, and GL/EGL/VA-API from `mesa-2404` at
  `$SNAP/gpu-2404`. `pack.py` checks every soname the payload links against
  against those trees before it packs, so a library that is in none of them is
  a warning at pack time rather than a failed exec later.
- **`browser-sandbox` has to be connected by hand.** Gecko builds its content
  sandbox out of unprivileged user namespaces, which snapd permits only
  through `browser-support` with `allow-sandbox: true` — and an interface
  declared that way never auto-connects. The launcher checks with `snapctl`
  and, if it is not connected, sets `MOZ_DISABLE_CONTENT_SANDBOX=1` and says
  so on stderr rather than letting every content process die at startup.
  snapd/AppArmor still confine the browser either way, but connect it:
  `sudo snap connect zen:browser-sandbox`.
- **The in-place updater is removed** (`updater`, `updater.ini`, and the
  `precomplete` and `removed-files` manifests it works from) and
  `policies.json` sets `DisableAppUpdate` — a squashfs is read-only, so an
  update Zen downloaded could never be applied. Rebuild with `snapkit build zen` to
  move to a new release. `DontCheckDefaultBrowser` is set for the same kind of
  reason: the check writes host settings the snap cannot reach.
- **The profile lives in `~/snap/zen/common/.zen`,** not in the versioned
  `~/snap/zen/current/`. Gecko reads `$HOME/.zen`, and `$SNAP_USER_DATA` is
  per revision, so every refresh would copy a whole browser profile forward;
  the launcher points `HOME` at `$SNAP_USER_COMMON` instead, which is what the
  official Firefox snap does. The trade-off is that `snap revert` does not
  take the profile back with it — which for a browser profile is the wanted
  behaviour anyway. To carry over an existing profile, copy `~/.zen` there
  after the first launch.
- **A `dbus` session slot for `org.mozilla.zen`.** Gecko's remoting — handing
  a URL to an already running window instead of starting a second copy — owns
  `org.mozilla.<RemotingName>`, and `application.ini` spells that name `zen`.
  snapd's `dbus` interface grants the well-known name and its children, which
  is what the per-profile suffix Gecko appends needs.
- **Interfaces granted:** desktop, desktop-legacy, gsettings, opengl, wayland,
  x11, unity7, browser-sandbox, network, network-bind, home, removable-media,
  audio-playback, audio-record, camera, cups-control, joystick,
  hardware-observe, mount-observe, screen-inhibit-control, upower-observe,
  u2f-devices, password-manager-service.
- **What does not work:** text-to-speech. Gecko dlopens `libspeechd.so.2`,
  which is in none of core24, `gnome-46-2404` or `mesa-2404`, so Zen's
  read-aloud is unavailable. Everything else the payload asks for is provided.

## Publishing to the store

This build is meant for local installs. It ships upstream's prebuilt binaries
instead of building from source, and the snap name would have to be registered
(and available) before anything could be uploaded.
