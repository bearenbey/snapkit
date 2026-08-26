# Godot snap (repack of the official editor zip)

Turns `Godot_v4.7.2-stable_linux.x86_64.zip` into a strictly confined snap
(`godot_4.7.2_amd64.snap`). snapcraft builds it from `snap/snapcraft.yaml`;
`pack.py` adds the checks a recipe cannot express and then runs it.

## Build

```sh
snapkit build godot
```

`snapkit build godot` packs whatever the project points at now. Moving it
onto a newer release is the other command: `snapkit update godot` fetches the
zip, drops the superseded one, rewrites the version wherever this project
spells it out, and builds the result.

## Install / run

```sh
sudo snap install --dangerous godot_4.7.2_amd64.snap
godot
```

`--dangerous` is required because the snap is not signed by the store. To
remove: `sudo snap remove godot`.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: metadata, apps, plugs, and the parts snapcraft builds |
| `pack.py` | run snapcraft, then check the packed editor's version |
| `overlay/meta/gui/godot.desktop` | desktop entry snapd exports to the host menu |
| `overlay/meta/gui/godot.png` | upstream `main/app_icon.png` (128×128) |
| `overlay/bin/launcher` | the app entry point |

`snap/command-chain/desktop-launch` and `hooks-configure-fonts` come from
snapcraft's `gnome` extension, which the recipe asks for by name. They used to
be copied out of the installed `gnome-46-2404` snap at build time.

## Design notes

- **base `core24` + strict confinement.** The upstream editor binary is
  statically linked against everything except a set of `dlopen`ed system
  libraries. Those come from the two content snaps snapd auto-connects:
  `mesa-2404` at `$SNAP/gpu-2404` (Vulkan, EGL/GLES, libX11, libXext, libxcb,
  libwayland-*) and `gnome-46-2404` at `$SNAP/gnome-platform` (libXcursor,
  libXi, libXinerama, libXrandr, libXrender, libxkbcommon, libasound,
  libpulse, libfontconfig, libdbus, libudev).
- **libdecor is vendored.** It is the one `dlopen` target neither platform
  snap ships, and Godot's Wayland backend needs it for window decorations, so
  the noble `libdecor-0-0` and `libdecor-0-plugin-1-gtk` debs are unpacked
  into the snap. The GTK plugin is found through a layout that maps
  `/usr/lib/x86_64-linux-gnu/libdecor` to the staged copy; the GTK 3 the
  plugin links against comes from the GNOME platform. If the plugin is ever
  missing, `bin/launcher` falls back to `--display-driver x11`.
- **No `libspeechd`.** Neither platform snap ships it, so the editor's
  text-to-speech (`DisplayServer.tts_*`) is unavailable. Everything else works.
- **Interfaces granted:** desktop, desktop-legacy, gsettings, opengl, wayland,
  x11, unity7, network, network-bind, home, removable-media, audio-playback,
  audio-record, joystick, screen-inhibit-control. `audio-record` and
  `joystick` are not auto-connected — run
  `snap connect godot:joystick` / `snap connect godot:audio-record` if you
  need gamepad input or microphone capture.
- Projects under `$HOME` (and on removable media) are reachable, but hidden
  directories in `$HOME` are not — the `home` interface does not cover
  dotfiles. Editor settings live in `~/snap/godot/current/.config/godot/`
  rather than `~/.config/godot`; copy an existing profile there after the
  first launch to carry it over.

## Publishing to the store

This build is meant for local installs. It ships upstream's prebuilt binary
instead of building from source, and the snap name would have to be registered
(and available) before anything could be uploaded.
