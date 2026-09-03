# Unity Hub snap (repack of the official .deb)

Turns `UnityHubSetup-amd64.deb` into a classically confined snap
(`unityhub_3.21.1_amd64.snap`). snapcraft builds it from `snap/snapcraft.yaml`;
`pack.py` adds the checks a recipe cannot express and then runs it. This snap
is not published or endorsed by the upstream project.

## Build

```sh
snapkit build unityhub
```

`snapkit build unityhub` packs whatever the project points at now. Moving it
onto a newer release is the other command: `snapkit update unityhub` fetches the
deb, drops the superseded one, rewrites the version wherever this project
spells it out, and builds the result.

## Install / run

```sh
sudo snap install --dangerous --classic unityhub_3.21.1_amd64.snap
unityhub
```

`--dangerous` is required because the snap is not signed by the store, and
`--classic` because of the confinement choice described below. snapd refuses
the install if either flag is missing. To remove: `sudo snap remove unityhub`.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: metadata, apps, plugs, and the parts snapcraft builds |
| `pack.py` | run snapcraft, then check the packed snap's payload and host libraries |
| `overlay/bin/launcher` | the app entry point |

The icon is copied at build time out of the deb's
`usr/share/icons/hicolor/256x256/apps/unityhub.png`, as it ships no SVG.

## Design notes

- **Classic confinement, unlike the other snaps here.** Unity Hub is not a
  self-contained app; it is an installer and launcher. It downloads Unity
  Editors into `~/Unity/Hub/Editor` and executes them, and those editors in
  turn shell out to host toolchains: gcc/clang for IL2CPP, the Android
  SDK/NDK, JDK, and so on. Under strict confinement a launched editor would
  inherit the Hub's AppArmor profile with none of that on `PATH`, so editor
  launching is unreliable and native builds break. Classic gives the Hub the
  host filesystem and the same behaviour as the `.deb`, at the cost of the
  sandbox.
- **No `gnome-46-2404` / `mesa-2404` content snaps.** A classic snap links
  against the host's libraries directly, so the platform and GPU content
  interfaces the strict snaps in this repo rely on are neither needed nor
  usable. `pack.py` runs `ldd` over `unityhub-bin` and warns about anything
  the host is missing; all of the deb's `Depends` resolve on this machine.
- **No `layout:`, no `plugs:`.** Both are meaningless under classic
  confinement, because the app already sees the real root filesystem.
- **`--no-sandbox`.** This build ships no setuid `chrome-sandbox` helper, so
  Electron would fall back to the unprivileged-user-namespace sandbox, which
  snapd's AppArmor profile and Ubuntu's
  `kernel.apparmor_restrict_unprivileged_userns` can each block outright.
  Upstream's own `unityhub` wrapper decides this at runtime by
  reading `/proc/sys/kernel/unprivileged_userns_clone`; `overlay/bin/launcher`
  replaces that wrapper and disables the sandbox unconditionally, which is what
  the other Electron snaps on this system do.
- **Config lives in the normal place.** Because confinement is classic, `$HOME`
  is the real home directory, so the Hub uses `~/.config/UnityHub` and
  `~/Unity` just like the deb, so no `~/snap/unityhub/` migration is needed, and an
  existing Hub install is picked up as-is.
- **`unityhub://` links** are registered through the exported desktop entry's
  `MimeType`, which is what the browser hands back after a sign-in.

## Things the deb did that the snap does not

The deb's `postinst` is not run, by design:

- **No APT repository or signing key.** The deb adds
  `/etc/apt/sources.list.d/unityhub.sources` so `apt` keeps the Hub updated.
  A snap cannot, so updates mean downloading a newer deb and re-running
  `pack.py`. Bump `version:` in `snap/snapcraft.yaml` to match, or the
  build warns about the mismatch.
- **No `/usr/bin/unityhub` alternative.** snapd provides `/snap/bin/unityhub`.
- **No host AppArmor profile.** The deb installs
  `/etc/apparmor.d/unityhub` from `resources/apparmor-profile`; snapd manages
  its own profile for the snap instead.
- The Hub may still advertise its own updates in the UI. Ignore those and
  rebuild from a newer deb.

## Publishing to the store

This build is meant for local installs. It ships upstream's prebuilt,
proprietary binaries instead of building from source, the snap name would have
to be registered before anything could be uploaded, and classic confinement
additionally requires manual approval from the store reviewers.
