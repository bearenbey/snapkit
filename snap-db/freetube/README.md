# FreeTube snap (repack of the official .deb)

Turns `freetube_0.25.3_beta_amd64.deb` into a strictly confined snap
(`freetube_0.25.3-beta_amd64.snap`). snapcraft builds it from
`snap/snapcraft.yaml`; `pack.py` adds the checks a recipe cannot express and
then runs it.

## Build

```sh
snapkit build freetube
```

`snapkit build freetube` packs whatever the project points at now. Moving it
onto a newer release is the other command: `snapkit update freetube` fetches the
deb, drops the superseded one, rewrites the version wherever this project
spells it out, and builds the result.

## Install / run

```sh
sudo snap install --dangerous freetube_0.25.3-beta_amd64.snap
snap connect freetube:password-manager-service   # optional, see below
freetube
```

`--dangerous` is required because the snap is not signed by the store. To
remove: `sudo snap remove freetube`.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: metadata, apps, plugs, and the parts snapcraft builds |
| `pack.py` | check the deb's version and payload layout, then run snapcraft |
| `overlay/bin/launcher` | the app entry point |

`snap/command-chain/desktop-launch` and `hooks-configure-fonts` come from
snapcraft's `gnome` extension, which the recipe asks for by name. They used to
be copied out of the installed `gnome-46-2404` snap at build time.

## Design notes

- **base `core24` + strict confinement.** GTK, GLib, fontconfig, pulse and the
  rest come from the `gnome-46-2404` content snap mounted at
  `$SNAP/gnome-platform`; graphics come from `mesa-2404` at `$SNAP/gpu-2404`.
  Both were already installed, and snapd auto-connects them.
- **`--no-sandbox`.** Electron's `chrome-sandbox` helper needs the setuid bit,
  which a read-only squashfs cannot provide. Confinement is enforced by
  snapd/AppArmor instead. Every other Electron snap on this machine (Signal,
  Todoist, Proton Mail) does the same thing.
- **`--password-store=basic`** is passed only while
  `password-manager-service` is disconnected, so Chromium does not block on a
  keyring it cannot reach. Connect that interface if you want FreeTube's
  stored secrets in the system keyring.
- **Interfaces granted:** desktop, desktop-legacy, gsettings, opengl, wayland,
  x11, unity7, browser-support, network, network-bind, home, removable-media,
  audio-playback, screen-inhibit-control, password-manager-service.
- Data lives in `~/snap/freetube/current/` rather than
  `~/.config/FreeTube`. To carry over an existing profile, copy
  `~/.config/FreeTube` to `~/snap/freetube/current/.config/FreeTube` after the
  first launch.

## Publishing to the store

This build is meant for local installs. It ships upstream's prebuilt binaries
instead of building from source, and the snap name would have to be registered
(and available) before anything could be uploaded.
