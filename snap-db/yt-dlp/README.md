# yt-dlp snap

A strictly confined snap of [yt-dlp](https://github.com/yt-dlp/yt-dlp)
2026.08.19, packaged from the upstream standalone Linux build
(`yt-dlp_linux.zip`). That zip is a PyInstaller bundle, so the snap carries no
Python of its own. It is not published or endorsed by the yt-dlp project.

ffmpeg is bundled with it. yt-dlp needs ffmpeg to merge separate video and
audio streams, which is what YouTube serves for anything above 720p, so
without it most downloads would fail at the last step.

## Build

```sh
snapkit build yt-dlp
```

`snapkit build yt-dlp` packs whatever the recipe points at now. Moving it onto
a newer release is the other command: `snapkit update yt-dlp` repoints the
source URL, rewrites the checksum, and builds the result.

## Install

The snap is unsigned and built locally, so it needs `--dangerous`:

```sh
sudo snap install --dangerous ./yt-dlp_2026.08.19_amd64.snap
```

`home`, `network` and `removable-media` are plugged. `home` and `network`
auto-connect; `removable-media` does not, so connect it if you download to
`/media` or `/run/media`:

```sh
sudo snap connect yt-dlp:removable-media
```

## Where it can write

Strict confinement means yt-dlp only sees your home directory, so run it from
somewhere under `$HOME`. Downloading into `/tmp` or `/srv` is denied, and it
surfaces as a permission error rather than as anything yt-dlp explains well.

Hidden directories in `$HOME` are not covered by the `home` interface either,
so `-o '~/.videos/%(title)s.%(ext)s'` will not work.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: the release zip, ffmpeg, and the launcher |
| `launcher/yt-dlp` | puts ffmpeg's subdirectory libraries on the search path |

## Design notes

- **The upstream zip is used as it is.** It records the executable bit on
  `yt-dlp_linux`, so nothing has to `chmod` it at build time, and it unpacks as
  the binary beside its `_internal/` directory rather than inside a wrapper
  directory that would have to be folded away.

- **ffmpeg comes from the archive rather than from the ffmpeg snap.** A snap
  cannot reach another snap's binaries, so the choice is bundling it or leaving
  merging broken. It is staged with its docs and man pages pruned.

- **The launcher exists for ffmpeg's libraries, not for `PATH`.** snapcraft
  already puts `$SNAP/usr/bin` on `PATH`, so yt-dlp finds the binary. What it
  cannot find is `libpulsecommon`, BLAS and LAPACK, which Debian keeps in
  subdirectories of the library directory that nothing searches. Without those
  three on `LD_LIBRARY_PATH` the bundled ffmpeg does not start at all, and
  yt-dlp reports `exe versions: none` rather than anything useful.

- **Updates are checked against upstream's own checksums.** Every release
  publishes `SHA2-256SUMS` beside its assets and the record points at it, so a
  download is verified against what yt-dlp published rather than only against
  itself.
