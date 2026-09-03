# Floorp snap (repack of the official tarball)

Turns `floorp-linux-x86_64.tar.xz` into a strictly confined snap
(`floorp_12.17.2_amd64.snap`). snapcraft builds it from `snap/snapcraft.yaml`;
`pack.py` adds the checks a recipe cannot express and then runs it. This snap
is not published or endorsed by the upstream project.

## Build

```sh
snapkit build floorp
```

`snapkit build floorp` packs whatever the project points at now. Moving it
onto a newer release is the other command: `snapkit update floorp` fetches the
tarball, rewrites the version wherever this project spells it out, and builds
the result.

The tarball's name carries no version, so upstream overwrites it in place
every release; which release is packaged is read back out of the payload's own
`application.ini` and checked against `snap/snapcraft.yaml` before packing.

## Install / run

```sh
sudo snap install --dangerous floorp_12.17.2_amd64.snap
sudo snap connect floorp:browser-sandbox   # see below — not optional in practice
floorp
```

`--dangerous` is required because the snap is not signed by the store. To
remove: `sudo snap remove floorp`.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: metadata, apps, plugs, and the parts snapcraft builds |
| `pack.py` | run snapcraft, then check the packed snap's version and libraries |
| `overlay/meta/gui/floorp.desktop` | desktop entry snapd exports to the host menu |
| `overlay/bin/launcher` | the app entry point |
| `overlay/usr/lib/floorp/distribution/policies.json` | enterprise policy that turns the updater off |

The icon is lifted out of the payload at build time
(`browser/chrome/icons/default/default128.png`); the tarball ships no hicolor
tree and no desktop entry, so the entry above is written here rather than
repointed from upstream's.

`snap/command-chain/desktop-launch` and `hooks-configure-fonts` come from
snapcraft's `gnome` extension, which the recipe asks for by name. They used to
be copied out of the installed `gnome-46-2404` snap at build time.

## Design notes

- **base `core24` + strict confinement.** GTK, GLib, fontconfig, NSS's system
  half, alsa and the rest come from the `gnome-46-2404` content snap mounted
  at `$SNAP/gnome-platform`; graphics and the X11 libraries come from
  `mesa-2404` at `$SNAP/gpu-2404`. Nothing is staged into the snap itself:
  every soname the payload needs resolves against those two plus `core24`.
  `pack.py` checks that rather than asserting it. It walks the payload's
  `NEEDED` entries against what the three snaps offer and warns about anything
  nothing provides, because a missing soname surfaces as a bare exec failure
  at launch rather than as a pack error.
- **`browser-sandbox` has to be connected by hand.** Gecko sandboxes its
  content processes with unprivileged user namespaces, which snapd's default
  policy denies; the interface that allows them is `browser-support` with
  `allow-sandbox: true`, and an interface in that shape never auto-connects
  for a local `--dangerous` install. While it is disconnected `bin/launcher`
  sets `MOZ_DISABLE_CONTENT_SANDBOX=1` and says so on stderr. The browser
  runs with snapd/AppArmor confinement alone instead of having every content
  process die at startup. Connect it and that layer comes back.
- **The updater is removed and disabled.** It cannot rewrite a read-only
  squashfs, so all it can produce is a restart prompt that changes nothing.
  `distribution/policies.json` sets `DisableAppUpdate`, and `updater`,
  `updater.ini`, `precomplete` and `removed-files`, the machinery that would
  have carried an update out, are dropped from the payload. Updates come from
  `snapkit update floorp` instead.
- **`HOME=$SNAP_USER_COMMON`, set in the launcher.** snapd copies
  `$SNAP_USER_DATA` into the new revision on every refresh, and a browser
  profile with its caches is large enough that this is worth avoiding;
  `$SNAP_USER_COMMON` is shared across revisions and is not copied. Mozilla's
  own firefox snap does the same, and it means the profile lives in
  `~/snap/floorp/common/.floorp`. It is exported from `bin/launcher` rather
  than declared in `meta/snap.yaml` on purpose: snapd exports that environment
  *before* the command chain, and `desktop-launch` relocates `~/.config` out
  from under an app whose `HOME` it does not recognise. By the time the
  launcher runs it has already pinned the XDG directories, so this moves the
  profile and nothing else.
- **A `dbus` session slot for `org.mozilla.floorp`.** Gecko's remoting, which
  hands a URL to a running window instead of starting a second copy, owns
  `org.mozilla.<RemotingName>`, and `application.ini` spells that name
  `floorp`. snapd's `dbus` interface grants the well-known name and its
  children, which is what the per-profile suffix Gecko appends needs.
- **A `/usr/share/hunspell` layout.** Gecko's spell checker reads its
  dictionaries from that path and no other; the binding points it at the
  platform snap's copy.
- **What does not work:** text-to-speech. Gecko dlopens `libspeechd.so.2`,
  which is in none of core24, `gnome-46-2404` or `mesa-2404`, so read-aloud is
  unavailable. A dlopen leaves no `NEEDED` entry, so this is the one gap the
  library check above cannot see; everything the payload links against is
  provided.
- **Interfaces granted:** desktop, desktop-legacy, gsettings, opengl, wayland,
  x11, unity7, browser-sandbox, network, network-bind, home, removable-media,
  audio-playback, audio-record, camera, cups-control, hardware-observe,
  joystick, mount-observe, screen-inhibit-control, password-manager-service,
  system-packages-doc, u2f-devices, upower-observe.
- To carry over an existing profile, copy `~/.floorp` to
  `~/snap/floorp/common/.floorp` after the first launch.

## Publishing to the store

This build is meant for local installs. It ships upstream's prebuilt binaries
instead of building from source, and the snap name would have to be registered
(and available) before anything could be uploaded.
