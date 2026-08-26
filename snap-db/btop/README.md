# btop snap

A strictly confined snap of [btop++](https://github.com/aristocratos/btop) 1.4.7,
packaged from the upstream statically linked musl x86_64 release tarball
(`btop-x86_64-unknown-linux-musl.tar.gz`). Because the binary is static, the snap
carries no libraries of its own and works on any base.

## Install

The snap is unsigned and built locally, so it needs `--dangerous`:

```sh
sudo snap install --dangerous ./btop_1.4.7_amd64.snap
```

btop reads information about the whole system, and the interfaces that allow
that are not auto-connected for a locally installed snap. Connect them:

```sh
sudo snap connect btop:system-observe    # /proc for every process
sudo snap connect btop:process-control   # send signals / renice from the UI
sudo snap connect btop:hardware-observe  # disk devices, temperature sensors
sudo snap connect btop:mount-observe     # mount table, for the disks panel
sudo snap connect btop:network-observe   # per-interface network counters
```

`home` and `removable-media` are also plugged, for disk usage of paths under
your home directory and under `/media`. `home` auto-connects; `removable-media`
does not.

Without `system-observe` btop starts but only sees its own process, so connect
at least that one.

Then run `btop`.

## Known issues

**Fixed: SIGSEGV on startup.** Earlier revisions of this snap crashed with
SIGSEGV a moment after drawing their first frame. The cause was a missing
`network` plug: `getifaddrs()` needs a netlink socket, snapd's seccomp filter
denies that without `network`, and btop then segfaults inside `Net::collect()`
instead of degrading gracefully. btop's own log made it visible:

    ERROR: Net::collect() -> getifaddrs() failed with id -1

Disabling just the net box (`shown_boxes = "cpu mem proc"`) made the crash go
away, which confirmed it. `network` auto-connects, so the fix needs no
`snap connect`. The underlying fragility is upstream's, since btop should
survive a failing `getifaddrs()`, so it is worth reporting to the btop
project.

The `update.go:193: cannot change mount namespace ... /boot ... permission
denied` message that accompanies startup is unrelated and harmless: the same
`/boot` bind entry is present in the mount profiles of `discord`, `steam` and
`canonical-livepatch`, which all run normally.

**Empty disks panel until `mount-observe` is connected.** Without it btop logs
`Failed to get mounts from /etc/mtab and /proc/self/mounts`. Likewise the CPU
temperature readout needs `hardware-observe`, which otherwise logs
`No good candidate for cpu sensor found`. `Failed to read /etc/fstab` is
expected and harmless. The core24 base ships no `/etc/fstab`, and btop only
consults it when `use_fstab` is enabled.

**The disks panel reports the snap's root, not the host's.** Upstream builds
btop for its own snap with `ADDFLAGS="-D SNAPPED"`, which switches the disks
panel from `/` to `/mnt` because a snap sees the host root there. That is a
compile-time flag, so it cannot be applied to a repack of the prebuilt release
binary. Fixing it means building btop from source in the snap instead.

## Configuration

Snaps get a private home, so btop's config and user themes live in:

```
~/snap/btop/current/.config/btop/
```

not `~/.config/btop/`. The 36 bundled themes ship inside the snap and are
picked up automatically.

## Rebuilding

`snap/snapcraft.yaml` is the source of truth. With a build backend
(LXD or Multipass) installed:

```sh
snapcraft pack
```

`pack.py` runs that same build and then checks what came out of it:

```sh
snapkit build btop
```

This packs the binary that is already here. `snapkit update btop` fetches a
newer one first, and builds the result.

Prefer `snapcraft pack` when a backend is available.

## Layout notes

btop looks for its bundled themes at `../share/btop/themes` relative to its own
executable, which is why the snap is laid out as `$SNAP/bin/btop` alongside
`$SNAP/share/btop/themes` rather than under a `usr/` prefix.
