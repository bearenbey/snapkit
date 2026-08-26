# ungoogled-chromium-snap

Snap packaging for
[ungoogled-chromium](https://github.com/ungoogled-software/ungoogled-chromium),
Chromium with Google web-service integration removed, built by repackaging
the official upstream amd64 portable tarball from
[ungoogled-chromium-portablelinux](https://github.com/ungoogled-software/ungoogled-chromium-portablelinux).

## Layout

```
pack.py                                              builds the snap
                                                     (`snapkit update ungoogled-chromium` fetches the release)
ungoogled-chromium-151.0.7922.173-1-x86_64_linux.tar.xz   upstream tarball (the source for the snap)
snap/snapcraft.yaml                                  snap recipe
snap/gui/ungoogled-chromium.desktop                  desktop entry
snap/gui/ungoogled-chromium.png                      256x256 icon
snap/local/ungoogled-chromium-launch                 launcher: sets Chromium env + profile dir
```

## Build

```sh
snapkit update ungoogled-chromium   # onto the newest release, and build it
snapkit build ungoogled-chromium    # build what the recipe points at now
```

`snapcraft` on its own also works, but it builds whatever tarball the recipe
currently points at, and it skips the check that the tarball really is the
release the recipe claims. A tag that does not exist answers with GitHub's
404 page rather than an error. `snapkit build` runs that check first.

Moving the recipe to a new release is the other command. `snapkit update
ungoogled-chromium` fetches the tarball, drops the superseded one, rewrites
the version wherever this project spells it out, and builds the result;
`snapkit check ungoogled-chromium` reports and writes nothing.

## Versioning

Upstream tags carry a packaging revision the Chromium version does not
(`151.0.7922.173-1`), and the snap is versioned by the whole tag: a rebuild of
the same Chromium against a newer upstream package is a release of its own.
`chrome --version` therefore reports the tag's leading component and not the
whole of it.

## Install

The snap is unsigned and not from the store, so it needs `--dangerous`.
It also declares `browser-support` with `allow-sandbox: true` (needed for
Chromium's namespace sandbox), which is not auto-connected, so connect it
manually:

```sh
sudo snap install --dangerous ungoogled-chromium_151.0.7922.173-1_amd64.snap
sudo snap connect ungoogled-chromium:browser-sandbox
sudo snap connect ungoogled-chromium:u2f-devices
```

Without `browser-sandbox` Chromium refuses to start rather than running
unsandboxed. `snapkit build ungoogled-chromium` does all three.

The remaining hardware interfaces (`camera`, `audio-record`, `bluez`,
`removable-media`, `password-manager-service`, ...) are declared but not
auto-connected; `snap connections ungoogled-chromium` lists them.

## Profile and custom flags

The browser profile lives in `~/snap/ungoogled-chromium/common/chromium`,
which survives snap refreshes. For persistent command-line flags, create
`~/snap/ungoogled-chromium/common/chromium-flags.conf`, one flag per line
(`#` comments allowed):

```
--enable-features=VaapiVideoDecodeLinuxGL
--force-dark-mode
```

The launcher also passes `--password-store=basic` when no keyring is
reachable, which is otherwise a hang at startup, and sets
`--ozone-platform-hint=auto` on a Wayland session.

## Notes

* No `stage-packages`: every library the binary links against comes from the
  `gnome-46-2404` platform snap (nss, cups, alsa, glib, pango, atk, cairo,
  dbus, udev, xkbcommon), `mesa-2404` (gbm, X11) or `core24` (libgcc).
* Upstream's `chrome-wrapper` is dropped at prime time, because it rewrites
  `LD_LIBRARY_PATH` and writes a stray `.desktop` file into `$HOME`, both
  wrong inside a snap. `snap/local/ungoogled-chromium-launch` replaces it.
* Upstream publishes no checksum file next to the release assets, so the
  download is recorded rather than verified against a published hash.
* This is an unofficial package, not affiliated with Google or with the
  ungoogled-chromium project.
