# Spotube snap (repack of the official .deb)

Turns `Spotube-linux-x86_64.deb` into a strictly confined snap
(`spotube_5.1.2_amd64.snap`). snapcraft builds it, the same way discord,
signal and sublimetext in this repo are built.

## Build

```sh
snapkit build spotube         # or: snapkit update spotube, to move it first
snapkit build spotube # after editing the recipe -- see below
```

The `cleanup` part deletes files out of the *shared* prime tree, and
snapcraft caches per-part lifecycle steps: on a second build it prints
`Skipping prime for spotube (already ran)` and never puts back what the
previous run's cleanup removed. So an incremental rebuild after editing the
recipe starts from an already-pruned tree and can succeed while packaging
something the recipe does not describe. Run `snapcraft clean` first whenever
the recipe changed.

`snapkit build spotube` builds whatever `.deb` is here now. Moving it onto a
newer release is the other command: `snapkit update spotube` fetches the
`.deb`, whose name carries no version so it is overwritten in place, and
builds the result.

## Install / run

```sh
sudo snap install --dangerous spotube_5.1.2_amd64.snap
snap connect spotube:password-manager-service   # optional, see below
snap connect spotube:avahi-control              # optional, for Connect
spotube
```

`--dangerous` is required because the snap is not signed by the store. To
remove: `sudo snap remove spotube`.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: unpack the deb, add libmpv and libappindicator, prune |
| `Spotube-linux-x86_64.deb` | upstream's release, replaced in place by `snapkit update` |

## Design notes

- **base `core24` + strict confinement, `gnome` extension.** GTK, GLib,
  fontconfig, WebKitGTK, libsecret and pulse come from the `gnome-46-2404`
  content snap; graphics come from `mesa-2404`. The extension wires up
  `desktop-launch`, the font cache hook and the `desktop`, `desktop-legacy`,
  `gsettings`, `opengl`, `wayland` and `x11` plugs, so only the extra
  interfaces are listed by hand in the recipe.
- **Two stage-packages, and only two.** Everything the Flutter payload links
  against is already in the platform except `libappindicator3.so.1`, which
  `tray_manager` needs and which the platform only carries in its Ayatana
  fork under a different soname. `libmpv.so.2` is the other one: `media_kit`
  *dlopens* it, so it appears in no `DT_NEEDED` entry and would have been
  missed by anything that inspects the binaries. Without it playback fails at
  the first track with a plugin initialisation error.
- **The `cleanup` part prunes against the runtime *search path*, not against
  the platform's file list.** A library the platform keeps in a subdirectory
  Debian's alternatives put BLAS and LAPACK in `blas/` and `lapack/` and
  point ldconfig at them via `/etc/ld.so.conf.d`, which is not staged and
  would not be consulted for `$SNAP` anyway, is *not* something the snap can
  resolve, so it does not count as provided. Libraries found in such a
  subdirectory are linked up into a directory that is searched, which is what
  ldconfig would have done. Exact-path pruning is applied to everything
  *except* libraries, for the same reason: whether a library can go is a
  question about the search path, not about where the file happens to sit.
- **A build-time guard resolves the whole `NEEDED` closure** against that
  same search path and fails the build if anything is unreachable. This is
  the part that earns its keep: name-based pruning that gets a name wrong
  does not fail during the build, it fails inside a `dlopen()` at runtime and
  blames the wrong thing. media_kit reports a missing libmpv when what is
  really missing is `libblas.so.3`, three levels below it, needed by
  `libsphinxbase`.
- **The platform snaps are read out of their squashfs images**, not out of
  `/snap/<name>/current`. Those snaps are installed in
  the build container but snapd never mounts them there. `/snap/core24/current`
  is a dangling symlink, so the usual `cd /snap/.../current && find .` idiom
  matches nothing and prunes nothing while looking like it worked.
- **Pruning is by soname, not by file name.** `libappindicator3-1` pulls in
  the whole of GTK and `libmpv2` the whole of ffmpeg, and the platform carries
  those at a different patch level (`libcairo.so.2.11804.4` against the
  archive's `libcairo.so.2.11800.0`), so a name-only pass keeps both copies
  and lets `LD_LIBRARY_PATH` order decide which GTK gets loaded. This took the
  snap from 80 MB to 73 MB and left exactly one GTK in it.
- **WebKit needs a nudge.** Its bubblewrap sandbox wants user namespaces
  that snapd's seccomp profile denies, so it is turned off with
  `WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS`; the snap as a whole is still
  confined by snapd/AppArmor. `WEBKIT_DISABLE_DMABUF_RENDERER` is set for the
  same reason other confined webviews set it. The fallback path is slower
  but always draws; drop it if your session renders fine without it. Both
  matter for the Spotify login window. The `layout:` entries that bind
  WebKit's libexec directory and libproxy into place are *not* here: the
  `gnome` extension already adds exactly those two.
- **`StartupWMClass=oss.krtirtho.spotube`.** `my_application_new()` picks
  the id `com.github.KRTirtho.Spotube` only when `container`, `FLATPAK_ID` or
  `FLATPAK` is set; in a snap none are, so the GTK application id, and with it
  the Wayland `app_id` the shell matches windows on, is `oss.krtirtho.spotube`.
  On X11 the class comes from `argv[0]` and is `spotube` instead; only one of
  the two can go in the desktop entry, and Wayland is the default session
  here. (The deb's desktop file also ends without a trailing newline, which
  is worth knowing before appending anything to it.)
- **Two D-Bus names, two slots.** A snap may only own session bus names tied
  to its own snap name, and Spotube wants two that are not. The `dbus` slot
  covers the GTK application id. Without it the app starts with
  `Failed to register: ... is not allowed to own the service
  "oss.krtirtho.spotube" due to AppArmor policy` and loses single-instance
  behaviour and shell integration. The `mpris` slot covers the player name:
  `audio_service_mpris` builds it as
  `org.mpris.MediaPlayer2.<dBusName>.instance<pid>` and the interface grants
  `org.mpris.MediaPlayer2.<name>{,.*}`, so the per-process suffix is covered.
  Owning a name works from the declaration alone, and neither slot has to be
  connected to anything.
- **Interfaces granted:** network, network-bind, home, removable-media,
  audio-playback, screen-inhibit-control, password-manager-service,
  browser-support, unity7, avahi-observe and avahi-control, plus the extension's.
  `password-manager-service` (Spotify credentials via libsecret) and
  `avahi-control` (advertising this device for Spotube Connect) are not
  auto-connected and need the `snap connect` lines above.
- **yt-dlp is not bundled.** Spotube can be pointed at a `yt-dlp` binary for
  some sources; the one in the `core24` archive is years stale and a stale
  yt-dlp fails in confusing ways. Install a current one under `$HOME` (the
  `home` interface lets the snap run it) and set the path in Spotube's
  settings.
- Data lives in `~/snap/spotube/current/` rather than `~/.local/share/spotube`.
  To carry over an existing profile, copy it there after the first launch.

## Publishing to the store

This build is meant for local installs. It ships upstream's prebuilt binaries
instead of building from source, and the snap name would have to be registered
(and available) before anything could be uploaded.
