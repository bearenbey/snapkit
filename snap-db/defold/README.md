# Defold snap (repack of the official editor zip)

Turns `Defold-x86_64-linux.zip` into a strictly confined snap
(`defold_1.13.1_amd64.snap`). snapcraft builds it from
`snap/snapcraft.yaml`; `pack.py` adds the checks a recipe cannot express and
then runs it. libopenal and libsndio come from `stage-packages` (see the
design notes); later builds reuse the cache and need no network.

## Build

```sh
snapkit build defold
```

`snapkit build defold` packs whatever the project points at now. Moving it
onto a newer release is the other command: `snapkit update defold` fetches the
zip, drops the superseded one, rewrites the version wherever this project
spells it out, and builds the result.

`pack.py` cross-checks the `version` in `snap/snapcraft.yaml` against the
`version` the zip's own `config` reports and refuses to pack if they differ,
so bumping the zip without bumping the metadata fails loudly.

## Install / run

```sh
sudo snap install --dangerous defold_1.13.1_amd64.snap
snap connect defold:joystick       # not auto-connected
defold
```

`--dangerous` is required because the snap is not signed by the store. To
remove: `sudo snap remove defold`.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: metadata, apps, plugs, and the parts snapcraft builds |
| `pack.py` | run snapcraft, then check the packed snap's version and OpenAL |
| `overlay/meta/gui/defold.desktop` | desktop entry snapd exports to the host menu |
| `overlay/bin/launcher` | the app entry point; pins JavaFX to X11 |

The icon is upstream's own `logo_blue.png`, copied out of the zip at build
time. `snap/command-chain/desktop-launch` and `hooks-configure-fonts` are
copied out of the installed `gnome-46-2404` snap — the same files snapcraft's
`gnome` extension pulls from the matching SDK.

## Design notes

- **The editor needs nothing staged; the engine needs OpenAL.** The editor
  bundles its own JDK 25 and all its JavaFX/JOGL natives, and the ~35 system
  libraries those pull in — GTK 3, gdk-3, pango, atk, cairo, gdk-pixbuf,
  libXtst, freetype, fontconfig, libasound — all come from `gnome-46-2404`,
  with libGL, libX11, libXext, libdrm, libgbm and libXxf86vm from
  `mesa-2404`. The *game engine* is the exception: `dmengine`, which the
  editor unpacks and runs on Build, links `libopenal.so.1`, which no provider
  snap ships, and libopenal in turn links `libsndio.so.7`. `pack.py` vendors
  those two out of the noble archive into `$SNAP/usr/lib/x86_64-linux-gnu`
  (already on `LD_LIBRARY_PATH`) and checks the digests. Noble, not the host:
  the base is core24, so a host `.deb` would link a newer glibc than the base
  provides. Below those two, the closure is covered again — libasound from
  `gnome-46-2404`, libbsd/libmd/libstdc++/libgcc_s from `core24`. The hrtf
  profiles from `libopenal-data` are staged too and reached through a
  `/usr/share/openal` layout, since OpenAL looks them up by absolute path.
- **JavaFX is pinned to X11.** `libglassgtk3.so` links `libgdk-3` and has no
  Wayland backend, so `bin/launcher` exports `GDK_BACKEND=x11` and the editor
  runs through XWayland. The `wayland` plug is still granted for the game
  engine the editor launches as a child process.
- **`XDG_STATE_HOME` and `user.home` are redirected.** Defold puts its logs
  and preferences in the XDG *state* directory
  (`com.defold.util.SupportPath#getLinuxSupportPath`), and `desktop-launch`
  redirects `XDG_CONFIG_HOME`/`XDG_DATA_HOME`/`XDG_CACHE_HOME` but not
  `XDG_STATE_HOME`, which is a newer part of the spec. The fallback is worse:
  the JVM does not read `user.home` from `$HOME`, it reads the passwd entry,
  so it resolves to the real home regardless of what snapd sets — and
  `~/.local/state` is a dot directory, which the `home` interface denies.
  `bin/launcher` therefore sets `XDG_STATE_HOME` *and* passes
  `-Duser.home=$SNAP_USER_DATA` through `JAVA_TOOL_OPTIONS`, so anything else
  keyed off `user.home` (`java.util.prefs`, the JavaFX native cache) also
  stays inside the sandbox.
- **The launcher finds itself.** Upstream's `Defold` binary resolves its
  resources path from `/proc/self/exe` (`dmSys::GetResourcesPath`), so
  dropping the tree at `$SNAP/opt/Defold` is enough — no path patching, and
  the launcher execs the real binary rather than symlinking to it.
- **Unpack-and-execute works.** The editor extracts its bundled natives and
  the game engine into its data directory and runs them from there; snapd's
  profile grants `mrkix` (read + inherit-execute) across
  `~/snap/defold/` with write on the current revision, which is exactly what
  that flow needs.
- **Self-update will not work.** The editor updates itself by writing a new
  jar into `packages/`, which is on a read-only squashfs. Update by dropping
  a newer zip in here and re-running `pack.py` instead.
- **Interfaces granted:** desktop, desktop-legacy, gsettings, opengl, x11,
  wayland, unity7, network, network-bind, home, removable-media,
  audio-playback, joystick, screen-inhibit-control. `joystick` is not
  auto-connected — run `snap connect defold:joystick` to test gamepad input.
- Projects under `$HOME` (and on removable media) are reachable, but hidden
  directories in `$HOME` are not — the `home` interface does not cover
  dotfiles. Editor preferences live under `~/snap/defold/current/`.

## Publishing to the store

This build is meant for local installs. It ships upstream's prebuilt editor
instead of building from source, and the snap name would have to be
registered (and available) before anything could be uploaded.
