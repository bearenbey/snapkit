# The shared recipe database

`snapkit db` reads a folder of recipes published in a git repository, so a
project packaged on one machine can be built on another without being
packaged again.

```sh
snapkit db                    what is published
snapkit db pull               write every project into the current directory
snapkit db pull zen godot     just those
snapkit install zen           fetch it, build it, and offer to install it
snapkit db publish <dir>      write the database out of the projects here
```

In the dashboard, `g` reads the database, says how many of its snaps are not
registered here, and asks before writing anything.

A snap is more than its `snapcraft.yaml`. Three of the twenty-five build from
the recipe alone, and the rest also need a launcher, an overlay tree, a
`pack.py` or a hook, so a project is published whole, minus the release it
was built from, the `.snap` it produced and any build tree. `index.json`
carries the file list, a sha256 and an executable bit for each, and the record
fields a project cannot tell you about itself: where its release comes from
and how an update reaches its packaging. Reading a project says what it
builds, never where it came from.

Nothing needs a token or git: the files are read over https from
`raw.githubusercontent.com`. `SNAPKIT_DB_URL` points at a different database,
which is how the tests reach one on disk.

A project whose recipe names a file too large to publish is marked incomplete
and refused by name, rather than pulled and left to fail at build time.

Every path in the index is checked before a byte is written. A file key that
is absolute, or that climbs out of the project with `..`, is refused and the
whole project is left unwritten rather than half of it. `build_with`, which
runs through a shell, is deliberately not a field the index may set: a project
that assembles itself does it with `pack.py`, which is a real file with a
sha256 in the index rather than a string nobody can see.

---

[Back to the README](../README.md)
