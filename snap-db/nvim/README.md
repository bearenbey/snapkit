# Neovim snap (repack of the official release tarball)

Turns `nvim-linux-x86_64.tar.gz` into a classically confined snap
(`nvim_0.12.5_amd64.snap`). snapcraft builds it from `snap/snapcraft.yaml`;
`pack.py` adds the checks a recipe cannot express and then runs it.

## Build

```sh
snapkit build nvim
```

`snapkit build nvim` packs whatever the project points at now. Moving it
onto a newer release is the other command: `snapkit update nvim` fetches the
tarball, drops the superseded one, rewrites the version wherever this project
spells it out, and builds the result.

`pack.py` cross-checks the `version` in `snap/snapcraft.yaml` against what
`nvim --version` reports and refuses to pack if they differ, so bumping the
tarball without bumping the metadata fails loudly.

## Install / run

```sh
sudo snap install --dangerous --classic nvim_0.12.5_amd64.snap
nvim
```

Both flags are required: `--dangerous` because the snap is not signed by the
store, and `--classic` because it declares classic confinement. To remove:
`sudo snap remove nvim`.

The snap is named `nvim` rather than `neovim` so the exported command is
plain `nvim`, because snapd only shortens `/snap/bin/<snap>.<app>` to
`/snap/bin/<snap>` when the app and the snap share a name.

## Layout

| Path | What it is |
| --- | --- |
| `snap/snapcraft.yaml` | the recipe: metadata, apps, plugs, and the parts snapcraft builds |
| `pack.py` | run snapcraft, then check the packed snap's version and host libraries |

The icon and the desktop entry are upstream's own, copied out of the tarball
at build time. Only `TryExec` is rewritten, to `/snap/bin/nvim`.

## Design notes

- **Classic, on purpose.** Strict confinement is close to useless for an
  editor. The `home` interface does not cover dotfiles, so `~/.config/nvim`
  would be unreadable and the config would have to be redirected into the
  snap's own data dir; `:!`, `:terminal` and every LSP client would see only
  the snap's `PATH`, so host language servers, formatters, compilers and
  `git` would not run; and files outside `$HOME`, such as `/etc`,
  would be off-limits. Classic keeps the real `$HOME`, the real `PATH` and
  the whole filesystem, which is what upstream's own `neovim` snap does too.
- **Nothing had to be staged.** Upstream links LuaJIT, tree-sitter, libuv,
  msgpack and unibilium statically; the binary's only `NEEDED` entries are
  `libm`, `libgcc_s` and `libc`, and the highest symbol version it asks for
  is `GLIBC_2.34`. A classic snap resolves those against the host rather than
  the base, and this host is well past that, but `pack.py` still runs `ldd`
  over the binary *and* every tree-sitter parser, because the parsers are
  `dlopen`ed and a missing dependency in one would otherwise surface only
  when a buffer of that filetype is opened.
- **No launcher wrapper.** Neovim resolves both its runtime and its parser
  directory relative to `/proc/self/exe`, so unpacking the tarball FHS-style
  under `usr/` is enough: `usr/bin/nvim` finds `usr/share/nvim/runtime` and
  `usr/lib/nvim/parser` on its own, with no path patching. `VIMRUNTIME` is
  still pinned in `snap.yaml` so a stray value inherited from the host shell
  cannot point the snap at a different Neovim's runtime files.
- **`base: core24` is nearly vestigial here.** A classic snap runs against
  the host filesystem, not the base, so core24 only sets the build-time
  expectations. It is declared for consistency with the other snaps in this
  collection.
- **Config, plugins and state stay on the host.** `~/.config/nvim`,
  `~/.local/share/nvim` and `~/.local/state/nvim` are the real ones, so an
  existing setup keeps working and plugin managers that shell out to `git`
  (lazy.nvim) or install servers into `~/.local/share/nvim/mason`
  (mason.nvim) behave normally. Nothing is redirected.
- **Self-update does not apply.** Neovim does not update itself; drop a newer
  tarball in here and re-run `pack.py`.
- **The man page is not exported.** `usr/share/man/man1/nvim.1` ships inside
  the snap, but snapd does not add snap man paths to `MANPATH`, so `man nvim`
  on the host will not find it. `:help` is unaffected.

## Publishing to the store

This build is meant for local installs. It ships upstream's prebuilt binary
rather than building from source, the snap name would have to be registered
first, and classic confinement additionally requires manual review before a
classic snap can be released.
