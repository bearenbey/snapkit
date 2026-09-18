# LibreWolf snap (repack of the official tarball)

Turns `librewolf-156.0-1-linux-x86_64-package.tar.xz` into a strictly
confined snap (`librewolf_156.0-1_amd64.snap`). snapcraft builds it from
`snap/snapcraft.yaml`; `pack.py` adds the checks a recipe cannot express and
then runs it. This snap is not published or endorsed by the LibreWolf
Community. LibreWolf is not associated with Mozilla, and Firefox is a
trademark of the Mozilla Foundation.

## Build

```sh
snapkit build librewolf
```

`snapkit build librewolf` packs whatever the project points at now. Moving
it onto a newer release is the other command: `snapkit update librewolf`
fetches the tarball, checks the detached signature beside it, drops the
superseded tarball, rewrites the version wherever this project spells it out,
and builds the result.

The upstream is the same one Flathub's `io.gitlab.librewolf-community`
package is built from: LibreWolf's own Gitea at `librewolf.dev` names the
release (`/api/v1/repos/librewolf/bsys6/releases/latest`), and the tarball
is on `dl.librewolf.net` under that version, with a `.sha256sum` and a
`.sig` beside it. The version is the release tag, `156.0-1`: the Firefox it
is built from plus LibreWolf's packaging revision.

The signature is made with the LibreWolf Maintainers key
(`662E3CDD 6FE32900 2D0CA5BB 40339DD8 2B12EF16`), which upstream publishes
at <https://librewolf.dev/librewolf.gpg>. `snapkit update` checks it when
that key is in your keyring and says so when it is not:

```sh
curl -fsSL https://librewolf.dev/librewolf.gpg | gpg --import
```

## Install / run

```sh
sudo snap install --dangerous librewolf_156.0-1_amd64.snap
sudo snap connect librewolf:browser-sandbox   # see below, not optional in practice
librewolf
```

`--dangerous` is required because the snap is not signed by the store. To
remove: `sudo snap remove librewolf`.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: metadata, apps, plugs, and the parts snapcraft builds |
| `pack.py` | run snapcraft, then check the packed snap's version, remoting name, update policy and libraries |
| `overlay/meta/gui/librewolf.desktop` | desktop entry snapd exports to the host menu |
| `overlay/bin/launcher` | the app entry point |

The icon is lifted out of the payload at build time
(`browser/chrome/icons/default/default128.png`); the tarball ships no hicolor
tree and no desktop entry, so the entry above is written here, after the one
Flathub carries for the same tarball.

`snap/command-chain/desktop-launch` and `hooks-configure-fonts` come from
snapcraft's `gnome` extension, which the recipe asks for by name.

## Design notes

- **base `core24` + strict confinement.** GTK, GLib, fontconfig, NSS's system
  half, alsa and the rest come from the `gnome-46-2404` content snap mounted
  at `$SNAP/gnome-platform`; graphics and the X11 libraries come from
  `mesa-2404` at `$SNAP/gpu-2404`. Nothing is staged into the snap itself.
  `pack.py` walks the payload's `NEEDED` entries against what the three snaps
  offer and warns about anything nothing provides, because a missing soname
  surfaces as a bare exec failure at launch rather than as a pack error.
- **No policy overlay, unlike the other Gecko snaps here.** floorp, zen,
  firefox-esr and waterfox each ship a `distribution/policies.json` of their
  own with `DisableAppUpdate` in it. LibreWolf's tarball already carries one,
  and it is most of what makes LibreWolf LibreWolf: uBlock Origin,
  HTTPS-only mode, no telemetry, no studies, no sponsored anything, and
  `DisableAppUpdate: true` among them. An overlay at the same path would
  replace the whole file. So the snap relies on upstream's, and `pack.py`
  reads that one policy back out of the packed payload and refuses a
  tarball where it is gone, since the updater cannot rewrite a squashfs and
  all it could produce is a restart prompt that changes nothing.
- **The updater is already gone.** LibreWolf strips `updater` and the crash
  reporter at build time; `precomplete` and `removed-files`, the manifests
  an update would have been applied against, are dropped from the prime.
- **The version is spelled the release's way.** The recipe, the register and
  the `.snap` say `156.0-1`, which is what the tag, the URL and the file
  name say; `application.ini` inside says `156.0`. `pack.py` strips the
  packaging revision before comparing, the way waterfox strips its Debian
  one.
- **`browser-sandbox` has to be connected by hand.** Gecko sandboxes its
  content processes with unprivileged user namespaces, which snapd's default
  policy denies; the interface that allows them is `browser-support` with
  `allow-sandbox: true`, and an interface in that shape never auto-connects
  for a local `--dangerous` install. While it is disconnected `bin/launcher`
  sets `MOZ_DISABLE_CONTENT_SANDBOX=1` and says so on stderr.
- **`HOME=$SNAP_USER_COMMON`, set in the launcher.** snapd copies
  `$SNAP_USER_DATA` into the new revision on every refresh, and a browser
  profile is large enough that this is worth avoiding. `application.ini`
  says `Profile=librewolf`, so the profile lives in
  `~/snap/librewolf/common/.librewolf`, which is also the directory the
  Flatpak persists. It is exported from `bin/launcher` rather than declared
  in the recipe on purpose: snapd exports that environment *before* the
  command chain, and `desktop-launch` relocates `~/.config` out from under an
  app whose `HOME` it does not recognise.
- **A `dbus` session slot for `org.mozilla.librewolf`.** Gecko's remoting
  owns `org.mozilla.<RemotingName>.<profile>`, and `application.ini` spells
  that name `librewolf`. snapd's `dbus` interface grants the name and its
  children. `pack.py` checks the name, since the slot and the desktop
  entry's `StartupWMClass` are both derived from it.
- **A `/usr/share/hunspell` layout.** Gecko's spell checker reads its
  dictionaries from that path and no other; the binding points it at the
  platform snap's copy.
- **What does not work:** text-to-speech. Gecko dlopens `libspeechd.so.2`,
  which is in none of core24, `gnome-46-2404` or `mesa-2404`. A dlopen leaves
  no `NEEDED` entry, so this is the one gap the library check cannot see.
  DRM is off as well, but that is LibreWolf's own policy
  (`EncryptedMediaExtensions: Enabled: false`), not the snap's.
- **Interfaces granted:** desktop, desktop-legacy, gsettings, opengl, wayland,
  x11, unity7, browser-sandbox, network, network-bind, home, removable-media,
  audio-playback, audio-record, camera, cups-control, hardware-observe,
  joystick, mount-observe, screen-inhibit-control, password-manager-service,
  system-packages-doc, u2f-devices, upower-observe.
- To carry over an existing profile, copy `~/.librewolf` to
  `~/snap/librewolf/common/.librewolf` after the first launch.

## Publishing to the store

This build is meant for local installs. It ships upstream's prebuilt binaries
instead of building from source, and the snap name would have to be registered
(and available) before anything could be uploaded.
