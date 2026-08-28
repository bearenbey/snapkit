# mpv-snap

A [snap](https://snapcraft.io/) package of [mpv](https://mpv.io/), the media
player for the command line and the desktop. This snap is not published or
endorsed by the upstream project.

Built from the upstream release tarball against `core26` (Ubuntu 26.04) in
**strict** confinement, with Lua and JavaScript scripting, VAAPI/VDPAU/NVDEC
hardware decoding, Vulkan and OpenGL video output, and `yt-dlp` bundled for
URL playback.

## Build

Requires `snapcraft` and a build backend (LXD is used by default):

```sh
snapkit build mpv
```

`snapkit build mpv` hands the recipe to `snapcraft pack` as it stands.
Moving it onto a newer release is the other command: `snapkit update mpv`
repoints the `source:` line at the new release, rewrites its checksum, and
builds the result.

This produces `mpv_<version>_amd64.snap`. Only `amd64` is built: the CUDA/NVDEC
hwaccel builds against nv-codec-headers, which Ubuntu packages for x86 only, so
another architecture would need that flag dropped.

## Install

The snap is not signed by the store, so a local build needs `--dangerous`:

```sh
sudo snap install --dangerous ./mpv_*.snap
```

Note on the name: this builds as `mpv`, which is fine for local installs but is
already registered in the Snap Store. Publishing would mean either claiming the
name from its current owner or renaming the snap. Change `name:` in
`snap/snapcraft.yaml` and the commands below follow.

## Video output

GPU userspace comes from Canonical's `mesa-2604` content snap rather than from
the host or from inside this snap:

```sh
sudo snap install mesa-2604
sudo snap connect mpv:gpu-2604 mesa-2604:gpu-2604
sudo snap connect mpv:opengl
```

`gpu-2604` supplies the OpenGL, EGL, Vulkan and VAAPI drivers; `opengl` grants
access to the `/dev/dri` device nodes. A store install would connect both
automatically. A local `--dangerous` install will not, so do it by hand.

This is wired up manually rather than through snapcraft's `gpu` extension on
purpose. That extension's command-chain wrapper **exits 3** when the content
snap is not connected, which would take out audio-only and terminal playback
too. The launcher here uses the GPU wrapper only if it is present.

No Mesa ships inside this snap. A full driver stack, `libgallium` and the
133 MB `libLLVM` behind it, arrives as a dependency of `libgbm1` and is
pruned again at prime time: the provider wrapper points GBM, EGL and VA-API at
`mesa-2604`'s copies regardless, and carrying a second Mesa build only invites
the two to disagree. The glvnd dispatch libraries stay, since they are what
the content snap's drivers plug into.

Without the connection, `--vo=gpu` and `--vo=gpu-next` have no driver to talk
to. These still work, and are worth knowing about on a headless machine or over
SSH:

| Video output | Needs |
| --- | --- |
| `--vo=xv` | an X server; the scaling happens in the server, not in a client driver |
| `--vo=sixel` | a terminal with sixel graphics (foot, mlterm, xterm `-ti vt340`) |
| `--vo=tct` | any true colour terminal |
| `--vo=caca` | any terminal, for the joke |
| `--vo=null --ao=pulse` | audio-only playback |

Check what the running snap can do:

```sh
mpv --vo=help
mpv --hwdec=help
mpv --gpu-api=vulkan --vo=gpu-next /path/to/file.mkv
```

## Hardware decoding

VAAPI, VDPAU and NVDEC/CUDA are all compiled in. VAAPI and Vulkan need the
`gpu-2604` connection above; NVDEC and CUDA `dlopen` the proprietary NVIDIA
driver at runtime, so nothing non-redistributable is linked into the snap and
they simply report as unavailable when no NVIDIA driver is present.

```sh
mpv --hwdec=auto-safe file.mkv          # let mpv pick
mpv --hwdec=vaapi     file.mkv          # Intel/AMD
mpv --hwdec=nvdec     file.mkv          # NVIDIA
```

On an NVIDIA-only machine VAAPI will fail to initialise: the proprietary driver
ships no VA-API implementation, so `/dev/dri/renderD*` exists but has nothing
usable behind it. That is not a packaging problem, so use `--hwdec=nvdec`.

## Streaming and yt-dlp

`yt-dlp` is bundled, so `mpv https://…` works out of the box. It is whatever
version Ubuntu 26.04 packaged, and streaming sites break yt-dlp far faster than
a snap rebuild can follow. A newer copy takes precedence without rebuilding
anything:

```sh
mkdir -p ~/snap/mpv/current/bin
curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
     -o ~/snap/mpv/current/bin/yt-dlp
chmod +x ~/snap/mpv/current/bin/yt-dlp
```

The launcher puts that directory first on `PATH`, which is where mpv's
`ytdl_hook` script looks. The standalone yt-dlp binary carries its own Python,
so nothing else has to be installed.

There is no `ffmpeg` binary in this snap, so yt-dlp cannot mux. It does not
need to: mpv plays the separate video and audio streams itself.

## Configuration

`$HOME` inside the snap is `~/snap/mpv/current`, so the configuration
directory is `~/snap/mpv/current/.config/mpv`, which is where `mpv.conf`,
`input.conf`, `scripts/` and `watch_later/` go.

To use your existing `~/.config/mpv` instead, connect the opt-in
personal-files plug:

```sh
sudo snap connect mpv:dot-config-mpv
```

The launcher points `MPV_HOME` at the real `~/.config/mpv` once that
connection makes it readable, and leaves an `MPV_HOME` you set yourself alone.
`personal-files` is a super-privileged interface, so it never auto-connects.

## Interfaces

| Interface | Auto-connects | What it is for |
| --- | --- | --- |
| `home` | yes | Playing files from `$HOME` |
| `network`, `network-bind` | yes | HTTP/RTSP/SRT streams, `--input-ipc-server` over TCP |
| `audio-playback` | yes | Sound, through the PulseAudio socket |
| `desktop`, `desktop-legacy` | yes | Host fonts, cursor themes, the exported desktop entry |
| `wayland`, `x11` | yes | The video window |
| `opengl` | yes | `/dev/dri` access for OpenGL, Vulkan and VAAPI |
| `screen-inhibit-control` | yes | Keeping the screensaver away during playback |
| `optical-drive` | yes (read-only) | `dvd://`, `bd://` and `cdda://` |
| `removable-media` | no | Files under `/media` and `/mnt` |
| `audio-record` | no | Capture sources, e.g. `av://alsa:default` |
| `alsa` | no | Raw ALSA devices, bypassing PulseAudio |
| `gpu-2604` | store installs only | Mesa drivers from `mesa-2604` |
| `dot-config-mpv` | never | Your real `~/.config/mpv` |

```sh
sudo snap connect mpv:removable-media
sudo snap connect mpv:audio-record
sudo snap connect mpv:alsa
sudo snap connect mpv:dot-config-mpv
```

## What is enabled

| Area | Included |
| --- | --- |
| Decoding | the system FFmpeg (libav*), so every codec Ubuntu's build carries |
| Video output | `gpu`, `gpu-next` (libplacebo), OpenGL and Vulkan, Wayland and X11, DRM/KMS, `xv`, `sixel`, `tct`, `caca` |
| Hardware decoding | VAAPI (DRM, Wayland, X11), VDPAU, NVDEC/CUDA |
| Audio output | PulseAudio, ALSA |
| Subtitles | libass, uchardet charset detection, FreeType/HarfBuzz/FriBidi |
| Scripting | Lua (LuaJIT), JavaScript (MuJS), C plugins, IPC |
| Streams and discs | libarchive, libbluray, dvdnav/dvdread, libcdio, yt-dlp |
| Filters and colour | zimg, rubberband, lcms2 |

Deliberately absent: PipeWire and JACK audio (no interface reaches those
sockets under strict confinement. PulseAudio is what `audio-playback`
exposes, and a PipeWire host answers on it), SDL2 (duplicates outputs mpv has
natively), VapourSynth, and the DVB input (needs `/dev/dvb`).

Neither CSS decryption nor AACS/BD+ keys are included, so encrypted discs will
not play.

## Confinement notes

* **Dot-directories.** The `home` interface deliberately does not cover hidden
  files at the top of your home directory, so `~/.local/share/videos/…` is
  unreachable while `~/Videos/…` is fine.

* **Fonts.** DejaVu is bundled and the launcher generates a small fontconfig
  file in `$SNAP_USER_DATA/.config` that lists both the snap's fonts and the
  host font directories the `desktop` interface shares in. Subtitles therefore
  render whether or not `desktop` is connected. A layout binding
  `/usr/share/fonts` would have been the obvious approach, but it collides
  with that same host font sharing and makes every launch fail with a
  `snap-update-ns` permission error.

* **The manual.** `-Dmanpage-build=disabled`, because `man` does not look
  inside snaps. `mpv --list-options`, `mpv --list-properties` and
  <https://mpv.io/manual/stable/> cover the same ground.

* **Sound.** snapd points `XDG_RUNTIME_DIR` at
  `/run/user/<uid>/snap.mpv`, and libpulse builds its socket path from that,
  so it looks for `snap.mpv/pulse/native`, which does not exist, and every
  connection is refused with no sound and no obvious reason. The launcher sets
  `PULSE_SERVER` to the real socket one directory up, which is what the
  `audio-playback` interface actually grants. A `PULSE_SERVER` you set
  yourself wins.

* **IPC sockets.** `--input-ipc-server=/tmp/mpvsocket` writes into the snap's
  private `/tmp`, so nothing outside can reach it. Put the socket somewhere
  under `~/snap/mpv/current` and point the other end at the same path.

## Updating to a new release

```sh
`snapkit update mpv`          # newest release on GitHub
`snapkit update mpv` 0.41.0   # a specific version
```

This rewrites `source:` and `source-checksum:` in `snap/snapcraft.yaml`. mpv
publishes no source tarball of its own. The release is the tag and GitHub
generates the archive, so there is nothing to verify a signature against and
the script computes the sha256 from the download itself. The snap version is
taken from the tarball's `MPV_VERSION` file at build time via `adopt-info`, so
it never needs editing by hand.

`snapkit update` does the same thing for every project in this directory, and
`snapkit update mpv` above is the entry point that touches only this one:

```sh
`snapkit update check` mpv
`snapkit update mpv` --build
```

## Licence

mpv is LGPL-2.1-or-later, but this build passes `-Dgpl=true`, which enables the
GPL-only parts of the player. The result is **GPL-2.0-or-later**, which is what
the snap declares.
