# snap-db

Recipes for snaps that snapkit knows how to build, kept here as plain files so
a project packaged on one machine can be built on another without being worked
out again.

```sh
snapkit db                    what is in here
snapkit db pull               write every project into the current directory
snapkit db pull zen godot     just those
snapkit install zen           fetch it, build it, and offer to install it
```

There is no token, no login and no git involved. snapkit reads these files
over https from raw.githubusercontent.com.

## What a project looks like

A snap is more than its `snapcraft.yaml`. Three of them build from the recipe
alone. The rest also need a launcher, an overlay tree, a `pack.py` or a hook,
and a recipe without those is a recipe that will not build. So each project is
kept whole:

```
snap-db/
    index.json
    zen/
        snap/snapcraft.yaml
        pack.py
        overlay/bin/launcher
        overlay/meta/gui/zen.desktop
        overlay/opt/zen/distribution/policies.json
        README.md
```

Three things are deliberately missing: the release the project was built from,
the `.snap` it produced, and any build tree. snapkit downloads the release
itself, so there is no reason to keep a copy here.

## index.json

The index is the whole of the protocol. A client reads it once and then knows
what exists, what each one is and which files to ask for. Adding a file to a
project needs no new client, and a client a version behind still works.

```json
{
  "schema": 1,
  "snaps": {
    "zen": {
      "name": "zen",
      "version": "1.21.15b",
      "summary": "A calmer way to browse the web",
      "upstream": "zen-browser/desktop",
      "fingerprint": "9f86d081...",
      "record": { "style": "artifact",
                  "asset_glob": "zen.linux-x86_64.tar.xz" },
      "files": {
        "snap/snapcraft.yaml": { "sha256": "...", "exec": false },
        "overlay/bin/launcher": { "sha256": "...", "exec": true }
      }
    }
  }
}
```

`record` is the part a project cannot tell you about itself. Reading a project
says what it builds. It never says where the release comes from, or how an
update reaches the packaging, and without that a pulled project has nothing to
build from.

`sha256` is checked on the way in. `exec` is recorded rather than guessed from
the file name, because a launcher that arrives without its executable bit is a
snap snapd will refuse.

`fingerprint` covers every file in the project. `snapkit db` compares it
against what is on disk and marks anything that has moved on, so you can see
at a glance whether this folder still matches the projects it came from.

## Projects that cannot be published whole

If a recipe names a file too large to keep here, the project is marked
`incomplete` and `snapkit db pull <name>` refuses it by name and says which
file is missing. Pulling everything skips it and carries on rather than
stopping. Nothing is currently in that state.

## Publishing

Written straight out of the projects on a machine that has them:

```sh
snapkit db publish path/to/snap-db
```

This file is written by that command, so edit it in `snapforge/snapdb.py`
rather than here.

Point `SNAPKIT_DB_URL` at somewhere else to use a fork, a private mirror or a
checkout on disk.
