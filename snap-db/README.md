# snap-db

Recipes for snaps that snapkit knows how to build, published as plain files so
that a project packaged on one machine can be built on another without being
packaged again.

```sh
snapkit db                    what is in here
snapkit db pull               write every project into the current directory
snapkit db pull zen godot     just those
snapkit install zen           fetch it, build it, and offer to install it
```

Nothing here needs a token, a login or git: snapkit reads these files over
https from `raw.githubusercontent.com`.

## What is in a project

A snap is more than its `snapcraft.yaml`. Three of the twenty-one build from
the recipe alone; the rest also need a launcher, an overlay tree, a `pack.py`
or a hook, and a recipe without them is a recipe that cannot build. So each
project is published whole:

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

What is **not** published: the release the project was built from, the `.snap`
it produced, and any build tree. snapkit downloads the release itself.

## index.json

The index is the whole of the protocol. A client reads it once and then knows
what exists, what each one is, and which files to ask for — so adding a file to
a project needs no new client, and a client a version behind still works.

```json
{
  "schema": 1,
  "snaps": {
    "zen": {
      "name": "zen",
      "version": "1.21.15b",
      "summary": "A calmer way to browse the web",
      "upstream": "zen-browser/desktop",
      "record": { "style": "artifact", "asset_glob": "zen.linux-x86_64.tar.xz" },
      "files": {
        "snap/snapcraft.yaml": { "sha256": "...", "exec": false },
        "overlay/bin/launcher": { "sha256": "...", "exec": true }
      }
    }
  }
}
```

`record` is what a project cannot tell you about itself. Reading a project says
what it builds; it never says where the release comes from or how an update
reaches the packaging. Without it a pulled project has nothing to build from.

`sha256` is checked on the way in. `exec` is recorded rather than guessed from
the file name — a launcher that arrives without its executable bit is a snap
that will not run, and snapd refuses it outright.

## Incomplete projects

A project whose recipe names a file too large to publish is marked
`"incomplete"` and refused by `snapkit db pull <name>`, with the missing file
named. Pulling everything skips it and says so rather than stopping.

Today that is `transmission`, which vendors a 16 MB gtkmm tarball.

## Publishing

Written straight out of the projects on a machine that has them:

```sh
snapkit db publish path/to/snap-db
```

`SNAPKIT_DB_URL` points snapkit at a different database — a fork, a private
mirror, or a checkout on disk.
