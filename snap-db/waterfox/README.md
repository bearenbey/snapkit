# Waterfox snap (repack of the official .deb)

Turns `waterfox_6.7.3-0_amd64.deb` into a strictly confined snap
(`waterfox_6.7.3-0_amd64.snap`). snapcraft builds it from
`snap/snapcraft.yaml`; `pack.py` adds the checks a recipe cannot express and
then runs it. This snap is not published or endorsed by BrowserWorks.

## Build

```sh
snapkit build waterfox
```

`snapkit build waterfox` packs whatever the project points at now. Moving it
onto a newer release is the other command: `snapkit update waterfox` fetches
the `.deb`, checks it against the SHA256 in the apt index, drops the
superseded one, rewrites the version wherever this project spells it out, and
builds the result.

The upstream is the apt repository BrowserWorks publishes through the
openSUSE Build Service
(`download.opensuse.org/repositories/isv:/BrowserWorks/xUbuntu_26.04/`).
It is a flat repository: the `Packages` index sits at its root and lists
amd64 and arm64 stanzas side by side, and the `.deb` is under `amd64/`.

## Install / run

```sh
sudo snap install --dangerous waterfox_6.7.3-0_amd64.snap
sudo snap connect waterfox:browser-sandbox   # see below, not optional in practice
waterfox
```

`--dangerous` is required because the snap is not signed by the store. To
remove: `sudo snap remove waterfox`.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: metadata, apps, plugs, and the parts snapcraft builds |
| `pack.py` | run snapcraft, then check the packed snap's version, remoting name and libraries |
| `overlay/bin/launcher` | the app entry point |
| `overlay/usr/lib/waterfox/distribution/policies.json` | enterprise policy that turns the updater off |

The desktop entry is the `.deb`'s own, `usr/share/applications/waterfox.desktop`;
the recipe only repoints its `Icon=` line, which names a theme icon that a
snap has no theme to find. The icon is lifted out of the payload at build
time (`browser/chrome/icons/default/default256.png`), which is larger than
anything in the `.deb`'s hicolor tree.

`snap/command-chain/desktop-launch` and `hooks-configure-fonts` come from
snapcraft's `gnome` extension, which the recipe asks for by name.

## Design notes

- **base `core24` + strict confinement.** GTK, GLib, fontconfig, NSS's system
  half, alsa and the rest come from the `gnome-46-2404` content snap mounted
  at `$SNAP/gnome-platform`; graphics and the X11 libraries come from
  `mesa-2404` at `$SNAP/gpu-2404`. Nothing is staged into the snap itself:
  every package in the `.deb`'s `Depends:` is in the platform already.
  `pack.py` walks the payload's `NEEDED` entries against what the three snaps
  offer and warns about anything nothing provides, because a missing soname
  surfaces as a bare exec failure at launch rather than as a pack error.
- **Two versions, and which one is checked.** Waterfox numbers its own
  releases (`6.7.3`) apart from the Gecko it is built on (`153.3.0`), and the
  `.deb` adds a Debian revision (`6.7.3-0`). The recipe, the register and the
  `.snap` say `6.7.3-0`, which is what the apt index says and what
  `snapkit check` compares against. `application.ini` only carries the Gecko
  version, so `pack.py` reads the release out of `MOZ_APP_VERSION_DISPLAY`
  in the payload's `omni.ja` and refuses a snap whose payload does not match
  the recipe with the revision stripped. It also checks the `.deb`'s control
  stanza against the recipe before snapcraft runs.
- **`browser-sandbox` has to be connected by hand.** Gecko sandboxes its
  content processes with unprivileged user namespaces, which snapd's default
  policy denies; the interface that allows them is `browser-support` with
  `allow-sandbox: true`, and an interface in that shape never auto-connects
  for a local `--dangerous` install. While it is disconnected `bin/launcher`
  sets `MOZ_DISABLE_CONTENT_SANDBOX=1` and says so on stderr.
- **The updater is removed and disabled.** It cannot rewrite a read-only
  squashfs. `distribution/policies.json` sets `DisableAppUpdate`, and
  `updater`, `updater.ini`, `precomplete` and `removed-files` are dropped from
  the payload. Updates come from `snapkit update waterfox` instead.
- **The `.deb`'s AppArmor profile is dropped.** `etc/apparmor.d/usr.bin.waterfox`
  is what the `.deb`'s postinst loads so that Ubuntu's user-namespace
  restriction lets the sandbox start; snapd writes this snap's profile itself,
  and `browser-sandbox` is the equivalent. The `update-alternatives` calls in
  the same postinst have no snap counterpart either.
- **`HOME=$SNAP_USER_COMMON`, set in the launcher.** snapd copies
  `$SNAP_USER_DATA` into the new revision on every refresh, and a browser
  profile is large enough that this is worth avoiding. The profile lives in
  `~/snap/waterfox/common/.waterfox`. It is exported from `bin/launcher`
  rather than declared in the recipe on purpose: snapd exports that
  environment *before* the command chain, and `desktop-launch` relocates
  `~/.config` out from under an app whose `HOME` it does not recognise.
- **A `dbus` session slot for `org.mozilla.waterfox`.** Gecko's remoting
  owns `org.mozilla.<RemotingName>.<profile>`, and `application.ini` spells
  that name `waterfox`. snapd's `dbus` interface grants the name and its
  children. `pack.py` checks the name, since the slot is derived from it.
- **A `/usr/share/hunspell` layout.** Gecko's spell checker reads its
  dictionaries from that path and no other; the binding points it at the
  platform snap's copy.
- **What does not work:** text-to-speech. Gecko dlopens `libspeechd.so.2`,
  which is in none of core24, `gnome-46-2404` or `mesa-2404`. A dlopen leaves
  no `NEEDED` entry, so this is the one gap the library check cannot see.
- **Interfaces granted:** desktop, desktop-legacy, gsettings, opengl, wayland,
  x11, unity7, browser-sandbox, network, network-bind, home, removable-media,
  audio-playback, audio-record, camera, cups-control, hardware-observe,
  joystick, mount-observe, screen-inhibit-control, password-manager-service,
  system-packages-doc, u2f-devices, upower-observe.
- To carry over an existing profile, copy `~/.waterfox` to
  `~/snap/waterfox/common/.waterfox` after the first launch.

## Publishing to the store

This build is meant for local installs. It ships BrowserWorks' prebuilt
binaries instead of building from source, and the snap name would have to be
registered (and available) before anything could be uploaded.
