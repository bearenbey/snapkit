# The register, and projects you already have

## The register


A directory, not a file. It lives at `~/.local/share/snapkit/`, or
`$SNAP_USER_COMMON/` inside the snap, or wherever `SNAPKIT_HOME` points:

```text
snaps/btop.json        what it is, where it came from, what to fetch
recipes/btop.yaml      the snapcraft.yaml, as text
icons/btop.svg         lifted out of the payload when it was made
```

It began as one JSON file holding all of it, which is the obvious thing and
does not last. Recipes are most of the weight. At sixteen snaps they were 78%
of the file, so every record carried several kilobytes almost nothing reads,
and a change to any field rewrote the lot. At a thousand snaps that is
5.5 MB reparsed by every command and rewritten by every `add`, which a create
does three times: about 130 ms of JSON per create, before any work.

Split up, at a thousand snaps:

| | one file | a directory |
| --- | --- | --- |
| read the register | 14.8 ms | 17.5 ms |
| change one snap | 27.7 ms | 0.1 ms |
| on disk, read to list | 5.5 MB | 0.95 MB |

Reading costs slightly more, a thousand small opens rather than one big
parse. In exchange, changing one snap no longer costs anything at all: it
writes one small file however many there are. The recipe is read only when
something asks for it, so listing a thousand snaps reads none of them.

Each piece stays a thing you can open: the record is legible JSON, the recipe
is a yaml file your editor already understands. A register from before this
changed is migrated on first use, and the old file is kept beside it as
`snapkit.json.migrated` rather than deleted.

Because it is meant to be edited, a record that cannot be read is set aside
rather than taken as the end of the register. One typo costs that record and
not the other nine hundred and ninety nine, and every command says which one
it was until it is fixed.

The register holds everything needed to rebuild:

- a project directory can be deleted and written out again from it
- paste a repository you have already used and it says so, rather than making
  a second copy of it
- removing a snap removes its record, its recipe and its icon

Two repositories whose names collapse to the same snap name are refused
rather than one replacing the other. There are a great many repositories
called `bat`. Pass `--name` to keep both; the dashboard picks a free
name and says so, rather than throwing away a build it has already made.

It is meant to be opened in an editor. So is the generated `snapcraft.yaml`:
edit it, and an update moves the version, the source URL and its checksum and
leaves the rest of your changes alone.

## Snap projects you already have


`snapkit import ../btop-snap` registers a project that exists already,
reading its recipe, version, summary and icon out of it. `./seed.py` does
that for every `*-snap` directory beside this one.

Nothing is inferred that cannot be read, and one thing that *can* be read is
deliberately not acted on: a repository worked out from a URL in a README is
recorded but left inert, because updating rewrites a recipe and repoints it
at a release. Pass `--repo owner/name` to confirm one and have that project
checked. `seed.py` holds those confirmations for the projects here.

Its table says the things a project cannot say about itself. Everything else,
the version, summary, licence, icon and the recipe, is read off the project:

| field | what it says |
| --- | --- |
| `repo` | the GitHub repository it packages, where that is the upstream |
| `upstream` | a shape out of `sources.py`, where it is not |
| `style` | `artifact` if the build opens a file sitting in the project, `recipe` if snapcraft fetches the source itself |
| `asset_glob` | matches that file in every release, so the superseded one is cleaned up |
| `local_asset` | what the build opens it as, where upstream's name is not it |
| `source_anchor` | which `source:` line an update repoints, so a second one is left alone |
| `checksums` | where upstream publishes the checksum, when it is not beside the file |
| `verify` | what a download is checked against before it is trusted |
| `pack` | the file exposing `build(project)`, for a project that wraps the build |

`asset_glob` and `local_asset` belong to the project rather than the upstream,
so they are named once and not repeated inside `upstream`. The exception is
the `local` shape, where the glob is also how the file is found in the first
place.

A project registered this way keeps its own build. All twenty-nine hand their
recipe to snapcraft, but sixteen of them wrap it in a `pack.py` that does the
work a recipe cannot express, and `snapkit build` imports that and calls its
`build(project)` with a `Build`:

```python
def build(project):                       # floorp-snap/pack.py
    tarball = project.artifact("floorp-linux-x86_64.tar.xz")
    built = project.pack()
    with project.unpacked(built) as root:
        app = root / "usr/lib/floorp"
        version = project.application_ini(app).get("App", "Version")
        project.check_version(version.partition("@")[0], "the packed payload")
        project.warn_unprovided(app, app / "floorp")
    return project.finish(built, "browser-sandbox", "u2f-devices")
```

Almost all of that work is one thing: refusing to ship a snap whose payload is
not the release the recipe claims. What every project does around snapcraft
to get there is on the `Build` rather than repeated in the file. `pack()` runs
snapcraft and finds the snap it made for the recipe's version, whatever the
architecture. `unpacked()` opens that snap for the checks, and a check that
dies inside the block deletes it, so a payload that failed is not left where
the next install finds it. `check_version()` is the refusal, told what the
payload should say where the recipe spells it differently. `finish()` says
how to install the result, `--classic` where the recipe asks for it and the
plugs that will not auto-connect. `artifact()` takes the file the recipe's
own `source:` names when one matches, so a superseded release beside the
current one is neither an ambiguity nor the one that gets built.

The questions more than one project asked are answered there too: a Gecko
payload's `application.ini`, a `.deb`'s control fields, the python modules
staged into a snap, what a staged library shadows in the gnome platform, and
what a strict payload links against that neither it nor the platform snaps
supply, read out of the ELF headers rather than out of `objdump`. What is
left in a `pack.py` is what only that project knows: where its binary lands,
what its `--version` prints, which plugs it needs, and why.

The dependency runs this way round on purpose. These scripts used to reach
the shared half of themselves along a relative path, which stops working the
moment the tool is installed as a snap, and meant every project spent four
lines on `sys.path` before it could say anything about itself. A `pack.py`
imports nothing: everything it can use is on the `project` it is handed,
`say`, `note`, `warn` and `die` included.

Nine of them are not a GitHub release at all: the download redirects Discord
and Mozilla's Firefox ESR answer, Emacs on a GNU mirror, ffmpeg.org, the apt
repositories Signal, Sublime Text and Unity publish through, and mpv and
RetroArch, which build from the archive GitHub rolls out of a tag rather
than from anything attached to the release. Those
are shapes rather than exceptions, and the shape is in the record:

```json
"upstream": {"kind": "apt", "base": "https://updates.signal.org/desktop/apt",
             "package": "signal-desktop", "index": "..."}
```

`sources.py` holds five of them: `apt`, `index`, `redirect`, `tag-archive` and
`local` for a snap made from a file, which watches the folder that file is in
-- and a record with no `upstream` is an ordinary GitHub release. So every
one of them is checked against its real upstream rather than reported as `not
tracked upstream`:

```console
$ snapkit check
NAME                 BUILT            UPSTREAM         STATUS
btop                 1.4.7            1.4.7            up to date
defold               1.13.1           1.13.1           up to date
discord              1.0.154          1.0.155          UPDATE AVAILABLE (discord-1.0.155.deb)
emacs                31.1             31.1             up to date
signal-desktop       8.24.1           8.24.1           up to date
...
```

---

[Back to the README](../README.md)
