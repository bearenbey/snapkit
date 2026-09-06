# Firefox ESR snap (repack of the official tarball)

Turns `firefox-140.15.0esr.tar.xz` into a strictly confined snap
(`firefox-esr_140.15.0esr_amd64.snap`). snapcraft builds it from
`snap/snapcraft.yaml`; `pack.py` adds the checks a recipe cannot express and
then runs it. This snap is not published or endorsed by Mozilla, and Firefox
is a trademark of the Mozilla Foundation.

## Build

```sh
snapkit build firefox-esr
```

`snapkit build firefox-esr` packs whatever the project points at now. Moving
it onto a newer release is the other command: `snapkit update firefox-esr`
fetches the tarball, checks Mozilla's detached signature, drops the superseded
tarball, rewrites the version wherever this project spells it out, and builds
the result.

The upstream is Mozilla's own ESR download endpoint
(`download.mozilla.org/?product=firefox-esr-latest-ssl`), which redirects to
the tarball Mozilla currently hands out as *the* ESR. Two ESR lines overlap for
about three months after a new one starts, and Mozilla keeps pointing at the
older one until the newer has had a few point releases, so this project moves
lines when Mozilla does rather than at the first `.0esr`.

## Install / run

```sh
sudo snap install --dangerous firefox-esr_140.15.0esr_amd64.snap
sudo snap connect firefox-esr:browser-sandbox   # see below, not optional in practice
firefox-esr
```

`--dangerous` is required because the snap is not signed by the store. To
remove: `sudo snap remove firefox-esr`.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: metadata, apps, plugs, and the parts snapcraft builds |
| `pack.py` | run snapcraft, then check the packed snap's version, channel and libraries |
| `overlay/meta/gui/firefox-esr.desktop` | desktop entry snapd exports to the host menu |
| `overlay/bin/launcher` | the app entry point |
| `overlay/usr/lib/firefox-esr/distribution/policies.json` | enterprise policy that turns the updater off |

The icon is lifted out of the payload at build time
(`browser/chrome/icons/default/default128.png`); the tarball ships no hicolor
tree and no desktop entry, so the entry above is written here rather than
repointed from upstream's.

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
- **`pack.py` refuses a tarball that is not ESR.** A mainline Firefox tarball
  unpacks to exactly the same layout and would pack cleanly, and the first
  file this project was handed was one (`firefox-155.0.tar.xz`). The two
  differ in `defaults/pref/channel-prefs.js`, which says `esr` or `release`,
  and in `application.ini`'s `RemotingName`, which is `firefox-esr` or
  `firefox`. The dbus slot and the desktop entry's `StartupWMClass` are
  derived from the second, so both are checked and a wrong one is refused.
- **The version is spelled the tarball's way.** The recipe, the register and
  the `.snap` say `140.15.0esr`, which is what Mozilla's URLs and file names
  say; `application.ini` inside says `140.15.0`. `pack.py` strips the suffix
  before comparing.
- **`browser-sandbox` has to be connected by hand.** Gecko sandboxes its
  content processes with unprivileged user namespaces, which snapd's default
  policy denies; the interface that allows them is `browser-support` with
  `allow-sandbox: true`, and an interface in that shape never auto-connects
  for a local `--dangerous` install. While it is disconnected `bin/launcher`
  sets `MOZ_DISABLE_CONTENT_SANDBOX=1` and says so on stderr.
- **The updater is removed and disabled.** It cannot rewrite a read-only
  squashfs. `distribution/policies.json` sets `DisableAppUpdate`, and
  `updater`, `updater.ini`, `precomplete` and `removed-files` are dropped from
  the payload. Updates come from `snapkit update firefox-esr` instead.
- **`HOME=$SNAP_USER_COMMON`, set in the launcher.** snapd copies
  `$SNAP_USER_DATA` into the new revision on every refresh, and a browser
  profile is large enough that this is worth avoiding. The profile lives in
  `~/snap/firefox-esr/common/.mozilla`. It is exported from `bin/launcher`
  rather than declared in the recipe on purpose: snapd exports that
  environment *before* the command chain, and `desktop-launch` relocates
  `~/.config` out from under an app whose `HOME` it does not recognise.
- **A `dbus` session slot for `org.mozilla.firefox_esr`.** Gecko's remoting
  owns `org.mozilla.<RemotingName>.<profile>`; the ESR build's remoting name
  is `firefox-esr`, and Gecko replaces the hyphen with an underscore because
  D-Bus names cannot carry one. snapd's `dbus` interface grants the name and
  its children. It is a different name from Mozilla's own `firefox` snap's,
  so the two can be installed side by side without one handing its URLs to
  the other.
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
- To carry over an existing profile, copy `~/.mozilla` to
  `~/snap/firefox-esr/common/.mozilla` after the first launch.

## Publishing to the store

This build is meant for local installs. It ships Mozilla's prebuilt binaries
instead of building from source, the `firefox` name in the store is
Mozilla's, and Mozilla's trademark policy does not allow a third party to
distribute unmodified builds under the Firefox name without permission.
