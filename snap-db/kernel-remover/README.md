# kernel-remover

A curses TUI that safely removes old Ubuntu kernels and what they leave
behind: the packages themselves, the configuration of packages already
removed, and kernel files in `/boot` that no package owns. It is one Python
file with no dependencies beyond the standard library, packaged here as a
classic snap. There is no upstream: this project is where the script lives.

```
 Kernel Remover                    running 6.8.0-45 | keep 2 | /boot free 412M
 Installed kernels ─────────────────────────────────────────────────────────
   🔒 6.8.0-47 (kept: newest)
   🔒 6.8.0-45 (kept: running)
   [x] 6.8.0-40  (old, 5 packages)                                     512M
     [x] linux-headers-6.8.0-40                                         89M
     [x] linux-headers-6.8.0-40-generic                                 21M
     [x] linux-image-6.8.0-40-generic                                   15M
     [x] linux-modules-6.8.0-40-generic                                346M
     [x] linux-modules-extra-6.8.0-40-generic                           41M

 Leftover configuration of removed kernel packages ─────────────────────────
     [x] linux-image-6.8.0-38-generic
 ...
 Selected: 5 packages, 1 config, 0 files  (~512M)
 ↑↓ move  Space toggle  a all  n none  +/- keep  r rescan  Enter apply  q quit
```

## Safety rules

These hold in every mode and cannot be switched off:

- the running kernel is never removed;
- the newest installed kernel is never removed;
- the N newest kernels are kept (`--keep`, default 2; `+` and `-` in the TUI);
- metapackages such as `linux-generic-hwe-24.04` are never touched, and if
  apt reports it would remove one as collateral the run is refused with the
  list of what it would take;
- packages on hold, whether by `apt-mark hold` or at the dpkg level, are
  never removed;
- nothing is removed without confirmation.

Before anything is purged the exact selection is passed to `apt-get -s
purge`. If the simulation removes or installs a single package you did not
tick, the run stops. That is what protects the metapackages: on a healthy system purging an
old kernel touches nothing else, and when it would, the right fix is
`sudo apt update && sudo apt full-upgrade`, not a forced removal.

## What it cleans

1. **Old kernel packages.** Every versioned `linux-*` package of a version
   outside the kept set: image, headers, modules, tools, `linux-hwe-6.8-*`,
   `linux-modules-nvidia-*`, DKMS signatures and objects. Ticked by default.
2. **Leftover configuration of removed kernel packages.** Packages dpkg lists
   as `rc`. These are offered even when there is no old kernel to remove.
   Ticked by default.
3. **Leftover configuration of any other removed package.** Off by default;
   `--all-rc` or tick them in the TUI.
4. **Orphaned files in `/boot`.** `vmlinuz-*`, `initrd.img-*`, `System.map-*`,
   `config-*` and `retpoline-*` files whose kernel version is not installed at
   all and that dpkg does not own. Off by default; `--purge-orphans` or tick
   them in the TUI. `update-grub` runs afterwards.

Files that belong to an installed kernel are never listed here, even ones
dpkg does not own (initrd images are generated, not shipped). Removing the
package takes them away.

## Usage

```sh
sudo kernel-remover                    # TUI
kernel-remover --dry-run               # TUI without root; apply only prints commands
sudo kernel-remover --plain            # no TUI, asks once
sudo kernel-remover --plain --yes      # non-interactive, defaults only
kernel-remover --help
```

| Option | |
| --- | --- |
| `-k N`, `--keep N` | newest kernel versions to keep (default 2); the running one is always kept |
| `-n`, `--dry-run` | print the commands instead of running them; no root needed |
| `-p`, `--plain` | plain text instead of the TUI; also used when not on a terminal |
| `-y`, `--yes` | plain mode: do not ask for confirmation |
| `--all-rc` | also select leftover configuration of non-kernel packages |
| `--purge-orphans` | also select orphaned `/boot` files |

TUI keys: arrows or `j`/`k` move, `PgUp`/`PgDn` and `Home`/`End` (or
`g`/`G`) jump, `Space` toggles an item or a whole group, `a` and `n` select
all or none, `+`/`-` (`=` works for `+`) change how many kernels to keep and
rescan, `r` rescans, `Enter` checks with apt and asks for confirmation, `q`
or `Esc` quits. A rescan puts the selection back to the default, so tick
things after choosing how many to keep.

The script runs without the snap too, on any Ubuntu with Python 3.8 or newer:

```sh
sudo ./snap/local/kernel_remover.py
```

## Building

    snapkit build kernel-remover

or, from the database on another machine, `snapkit install kernel-remover`.
Either way `pack.py` runs the script's tests first, then `snapcraft pack`,
then refuses the result unless it carries exactly the script and its
launcher. Plain `snapcraft pack` in this directory works as well, without
those checks.

## Installing what you built

The snap is not in the Snap Store: classic confinement needs a manual store
review, and the tool is only useful on the machine it is built on anyway.
Locally built snaps are unsigned, and classic ones need saying so:

    sudo snap install --dangerous --classic kernel-remover_1.1.0_amd64.snap

`sudo kernel-remover` then works, because Ubuntu's `secure_path` includes
`/snap/bin`.

## Why classic

The tool exists to run `apt-get purge`, `dpkg --purge` and `update-grub`
against the host, and to read the host's `/boot` and dpkg database. A
strictly confined snap sees none of that, so classic confinement is the
only option. The snap therefore carries no Python of its own: the launcher
runs the script with the host's `/usr/bin/python3`, which every Ubuntu
install has: the `ubuntu-minimal` seed depends on it.

## Layout

| Path | What it is |
| --- | --- |
| `snap/local/kernel_remover.py` | the tool; there is no other copy |
| `snap/local/kernel-remover-launch` | `exec /usr/bin/python3 $SNAP/lib/kernel-remover/kernel_remover.py "$@"` |
| `snap/local/tests.py` | tests for the planner; touch no apt, dpkg or `/boot` |
| `snap/snapcraft.yaml` | the recipe: one `dump` part, staged to the two files above |
| `pack.py` | `build(project)` for snapkit: tests, pack, check the payload |

The planner reads everything through one `Host` value (running kernel,
package list, holds, `/boot` listing, dpkg ownership), and the tests build
that by hand. Everything below it, from grouping packages by version to the
default selection and the apt collateral parser, is tested without root.

## Updating

There is nothing upstream to check, so `snapkit check` leaves this one
alone. A new version is an edit to `snap/local/kernel_remover.py`, a test if
the planner changed, a bump of `version:` in `snap/snapcraft.yaml`, and
`snapkit build kernel-remover`.
