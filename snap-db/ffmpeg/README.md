# ffmpeg-snap

A [snap](https://snapcraft.io/) package of [FFmpeg](https://ffmpeg.org/), the
cross-platform toolkit for recording, converting and streaming audio and video.

Built from the upstream release tarball against `core26` (Ubuntu 26.04) in
**strict** confinement, with a broad codec and filter set and with VAAPI,
VDPAU, Vulkan, Intel QSV and NVIDIA NVENC/NVDEC hardware acceleration.

## Build

Requires `snapcraft` and a build backend (LXD is used by default):

```sh
snapkit build ffmpeg
```

`snapkit build ffmpeg` hands the recipe to `snapcraft pack` as it stands.
Moving it onto a newer release is the other command: `snapkit update ffmpeg`
repoints the `source:` line at the new release, rewrites its checksum, and
builds the result.

This produces `ffmpeg_<version>_amd64.snap`. Only `amd64` is built: the Intel
QSV (`libvpl`) and NVENC configure flags assume x86, so other architectures
would need a trimmed flag list.

## Install

The snap is not signed by the store, so a local build needs `--dangerous`:

```sh
sudo snap install --dangerous ./ffmpeg_*.snap
```

Note on the name: this builds as `ffmpeg`, which is fine for local installs but
is very likely already registered in the Snap Store. Publishing would mean
either claiming the name from its current owner or renaming the snap. Change
`name:` in `snap/snapcraft.yaml` and the command prefixes below follow.

## Commands

The snap ships three tools. Only the one matching the snap name is exposed
unprefixed:

| Command | Runs |
| --- | --- |
| `ffmpeg` | the transcoder |
| `ffmpeg.ffprobe` | the stream analyser |
| `ffmpeg.ffplay` | the SDL based player |

The prefixes come from snapd, not from this package. A store-published snap can
request aliases so that `ffprobe` and `ffplay` work bare; for a local install
you can set them yourself:

```sh
sudo snap alias ffmpeg.ffprobe ffprobe
sudo snap alias ffmpeg.ffplay ffplay
```

## What is enabled

Built with `--enable-gpl --enable-version3`, and **without** `--enable-nonfree`,
so the result stays redistributable.

| Area | Included |
| --- | --- |
| Video | x264, x265, VP8/VP9 (libvpx), AV1 encode (SVT-AV1, libaom) and decode (dav1d), Xvid, OpenH264, WebP, OpenJPEG, JPEG XL |
| Audio | Opus, Vorbis, LAME, Theora, Speex, TwoLAME, GSM, Codec2, AMR-NB/WB |
| Subtitles and text | libass, FreeType, Fontconfig, FriBidi, HarfBuzz, aribb24, zvbi |
| Filters | zimg, rubberband, soxr, vidstab, mysofa, qrencode, chromaprint, libplacebo |
| Sources | Blu-ray, OpenMPT, GME, libcdio, dvdnav/dvdread |
| Protocols | GnuTLS, SSH, XML (DASH), SRT, RIST, ZeroMQ, Snappy, lzma/zlib/bzip2 |
| Devices | SDL2, PulseAudio, ALSA, JACK, V4L2, XCB (x11grab), libcaca |

Notable omission: **libfdk-aac**. It is licence-incompatible with GPL
redistribution, so it is deliberately absent. Use the built-in `aac` encoder.

To see exactly what the built snap supports:

```sh
ffmpeg -hide_banner -encoders
ffmpeg -hide_banner -filters
```

## Hardware acceleration

GPU userspace comes from Canonical's `mesa-2604` content snap rather than being
bundled:

```sh
sudo snap install mesa-2604
sudo snap connect ffmpeg:gpu-2604 mesa-2604:gpu-2604
sudo snap connect ffmpeg:opengl
```

`gpu-2604` supplies the VAAPI, Vulkan and OpenCL drivers; `opengl` grants
access to the `/dev/dri` device nodes. Installing from the store would connect
both automatically. A local `--dangerous` install will not, so do it by hand.

This is wired up manually rather than through snapcraft's `gpu` extension on
purpose. That extension's command-chain wrapper **exits 3** when the content
snap is not connected, which would break plain software transcoding too. The
launcher here uses the GPU wrapper only if it is present, so hardware
acceleration is a bonus rather than a hard requirement.

Verify what the running snap can see:

```sh
ffmpeg -hide_banner -hwaccels
ffmpeg -hide_banner -init_hw_device vaapi=hw:/dev/dri/renderD128 -f lavfi -i testsrc \
       -vf 'format=nv12,hwupload' -c:v h264_vaapi -f null -
```

NVENC/NVDEC and CUDA are compiled in but `dlopen` the proprietary driver
libraries at runtime, so nothing non-redistributable is linked into the snap.
They simply report as unavailable when no NVIDIA driver is present.

On an NVIDIA-only machine the VAAPI example above will fail with
`Failed to initialise VAAPI connection`. That is not a packaging problem: the
proprietary NVIDIA driver ships no VA-API implementation, so `/dev/dri/renderD*`
exists but has no usable VAAPI driver behind it. Use NVENC instead:

```sh
ffmpeg -f lavfi -i testsrc -t 5 -pix_fmt yuv420p -c:v h264_nvenc out.mp4
```

The `-pix_fmt yuv420p` there matters more than it looks. Synthetic sources like
`testsrc` produce RGB, and NVENC will happily encode that as H.264 **High
4:4:4 Predictive**. NVDEC cannot decode 4:4:4, so such a file then fails on
`-hwaccel cuda` with `Hardware is lacking required capabilities`, and
`av1_nvenc` rejects RGB input outright with `No capable devices found`. Both
messages look like missing hardware support and are really just the pixel
format. Real-world inputs are already yuv420p and do not hit this.

`kmsgrab` will not work: it needs `CAP_SYS_ADMIN`, which strict confinement
does not grant.

## Interfaces

| Interface | Auto-connects | What it is for |
| --- | --- | --- |
| `home` | yes | Reading and writing ordinary files in `$HOME` |
| `network`, `network-bind` | yes | HTTP/RTMP/RTSP/SRT input and output; `-listen 1` |
| `audio-playback` | yes | `ffplay` output, PulseAudio sinks |
| `opengl` | yes | `/dev/dri` access for VAAPI, Vulkan and QSV |
| `desktop`, `wayland`, `x11` | yes | `ffplay` windows, and `x11grab` screen capture |
| `optical-drive` | yes (read-only) | Audio CD and DVD input |
| `removable-media` | no | Files under `/media` and `/mnt` |
| `audio-record` | no | Microphone and monitor capture |
| `camera` | no | V4L2 webcams |
| `alsa` | no | Raw ALSA devices, bypassing PulseAudio |
| `hardware-observe` | no | Device enumeration during capture-device probing |
| `gpu-2604` | store installs only | Mesa drivers from `mesa-2604` |

Connect the opt-in ones as needed:

```sh
sudo snap connect ffmpeg:removable-media
sudo snap connect ffmpeg:audio-record
sudo snap connect ffmpeg:camera
sudo snap connect ffmpeg:alsa
sudo snap connect ffmpeg:hardware-observe
```

## Confinement notes

* **Dot-directories.** The `home` interface deliberately does not cover
  hidden files at the top of your home directory, so paths like
  `~/.local/share/videos/in.mkv` are unreachable. Ordinary paths are fine.

* **Working directory.** The tools run in your current directory, so relative
  paths behave normally. `~` is expanded by your shell before FFmpeg sees it,
  so it points at your real home either way.

* **`$HOME` inside the snap** is `~/snap/ffmpeg/current`. That is where the
  fontconfig cache and any FFmpeg dotfiles land.

* **Fonts.** DejaVu is bundled, and the launcher generates a small fontconfig
  file in `$SNAP_USER_DATA/.config` pointing at the snap's own font directory,
  so `drawtext` and libass work without host font access. A layout binding
  `/usr/share/fonts` would have been the obvious way to do this, but it
  collides with the host font sharing snapd sets up for the `desktop`
  interface and makes every launch fail with a `snap-update-ns` permission
  error. To use a different font, point at it explicitly with `fontfile=`.

## Updating to a new release

```sh
`snapkit update ffmpeg`          # newest release on ffmpeg.org
`snapkit update ffmpeg` 9.0.1    # a specific version
```

This rewrites `source:` and `source-checksum:` in `snap/snapcraft.yaml`.
FFmpeg publishes only a detached GPG signature next to each tarball, so the
script downloads the tarball, verifies the signature when your keyring already
trusts the FFmpeg release key, and computes the sha256 itself. The snap
version is taken from the tarball's `RELEASE` file at build time via
`adopt-info`, so it never needs editing by hand.

## Licence

FFmpeg is licensed under the LGPL-2.1-or-later, but this build enables GPL and
version-3 components (x264, x265, vidstab, rubberband, dvdread, AMR), which
makes the resulting binaries **GPL-3.0-or-later**. That is what the snap
declares.
