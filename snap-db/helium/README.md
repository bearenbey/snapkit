# helium-snap

Snap packaging for the [Helium browser](https://github.com/imputnet/helium-linux),
built by repackaging the official upstream amd64 `.deb`.

## Layout

```
pack.py                           assembles and builds the snap
                                  (`snapkit update helium` fetches the release)
helium-bin_0.15.7.1-1_amd64.deb   upstream .deb (the source for the snap)
snap/snapcraft.yaml               snap recipe
snap/gui/helium.desktop           desktop entry (taken from the .deb, Icon= repointed)
snap/gui/helium.png               256x256 icon (taken from the .deb)
snap/local/helium-launch          launcher: sets Chromium env + profile dir
```

## Build

```sh
snapkit update helium      # onto the newest release, and build it
snapkit build helium       # build what the recipe points at now
```

`snapcraft` on its own also works, but it builds whatever `.deb` the recipe
currently points at, and it skips the step that lifts the desktop entry and
icon out of that `.deb` rather than keeping a stale copy of them here.
`snapkit build helium` does that first.

Moving the recipe to a new release is the other command: `snapkit update
helium` fetches the `.deb`, drops the superseded one, rewrites the version
wherever this project spells it out, and builds the result. `snapkit check
helium` reports and writes nothing.

## Install

The snap is unsigned and not from the store, so it needs `--dangerous`.
It also declares `browser-support` with `allow-sandbox: true` (needed for
Chromium's namespace sandbox), which is not auto-connected, so connect it
manually:

```sh
sudo snap install --dangerous helium_0.15.6.1_amd64.snap
sudo snap connect helium:browser-sandbox
sudo snap connect helium:u2f-devices
snap connections helium          # check what else is unconnected
helium
```

`chromedriver` is included as a second app: `helium.chromedriver`.

### The connect step is not optional

`browser-support` with `allow-sandbox: true` is never auto-connected. If you
skip `snap connect`, snapd's default seccomp filter blocks `clone(CLONE_NEWUSER)`
and `ptrace`, and Helium dies at startup with:

```
FATAL:sandbox/linux/services/credentials.cc:131] Check failed: . : Permission denied (13)
ERROR:...crashpad/util/linux/scoped_ptrace_attach.cc:27] ptrace: Operation not permitted (1)
terminated by signal SIGTRAP
```

Fix it with `sudo snap connect helium:browser-sandbox`; confirm with
`snap connections helium` (the `browser-sandbox` row must not say `-`).

### Neither is `u2f-devices`, if you use a security key

`u2f-devices` is also manual-connect. Without it the snap has no access to the
`/dev/hidraw*` node of a YubiKey (or any FIDO key), so WebAuthn / 2FA prompts in
Helium find no security key at all. The browser shows no error, the key just
never blinks.

```sh
sudo snap connect helium:u2f-devices
```

Both interfaces are auto-connected for snaps published through the store with a
snap declaration (that is why the Proton snaps work out of the box); a local
`--dangerous` install gets no auto-connections and needs them by hand.

Note this covers the FIDO/U2F (hidraw) side only. The YubiKey's CCID smartcard
side, such as PIV and OpenPGP, goes through `pcscd` on the host and is not
reachable from strict confinement via this interface.

## Notes

* The browser profile lives in `~/snap/helium/common/helium` (set explicitly in
  the launcher) so that it survives `snap revert` and is not copied on refresh.
* Publishing to the Snap Store would need a registered name and a manual review
  request for `allow-sandbox: true`.

## Updating to a new upstream release

```sh
snapkit check helium              # is there anything new?
snapkit update helium             # move onto it, and build it
snapkit update helium --force     # redo the release it is already on
```

The build prints the `snap install --dangerous` and `snap connect` lines to
run afterwards; neither interface auto-connects for a local install.

`snapkit build helium` is still the whole update in one command; it is just no longer
all in one file. `snapkit update helium` does the first half:

1. Resolves the release tag (from the releases atom feed, which has no rate
   limit, unlike the REST API) and the matching `helium-bin_*_amd64.deb`
   asset name, including its debian revision.
2. Downloads the `.deb`, to a `.part` file that is only renamed once the
   transfer finishes. Upstream signs only the tarballs, so there is no
   checksum to verify this one against.
3. Rewrites `version:` and the part's `source:` in `snap/snapcraft.yaml` and
   the version references in this README, printing every line it changed, and
   deletes the `.deb` it superseded.

and `pack.py` the second, on whatever the recipe says by then:

4. Re-extracts `helium.desktop` and the 256x256 icon from the `.deb` into
   `snap/gui/`, repointing `Icon=` at `${SNAP}/meta/gui/helium.png`, and says
   which of them actually changed.
5. Runs `snapcraft clean && snapcraft`.

Other flags: `--no-clean` skips the `snapcraft clean` (faster, but stale parts
can leak into the build), `--keep-old` keeps the superseded `.deb` files, and
`--help` lists everything.

Nothing is modified until the download has succeeded, so a failed or
interrupted run leaves the checkout on the version it was already building.
