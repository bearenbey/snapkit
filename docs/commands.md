# Every command

| command | what it does |
| --- | --- |
| `snapkit` | the dashboard |
| `snapkit create <repo>` | make a snap from a repository |
| `snapkit create ./thing.deb` | or from a file you already have |
| `snapkit create ~/Downloads` | or from whichever of those is in there |
| `snapkit create` | and with nothing named, it asks |
| `snapkit package <name\|repo>` | build one already registered, from the register |
| `snapkit search <text>` | find one by name, repository or summary |
| `snapkit list` | what is registered |
| `snapkit show <name>` | one record, in full, including the recipe |
| `snapkit check [name ...]` | what has a newer release upstream |
| `snapkit update <name> [...]` | move onto that release and rebuild |
| `snapkit update <name> --force` | redo it even if it is already current |
| `snapkit track <name>` | where its releases are looked for, and what is there |
| `snapkit track <name> <kind> ...` | look for them somewhere that is not a release |
| `snapkit track <name> repo owner/name` | put it back on GitHub releases |
| `snapkit track <name> none` | stop checking it against anything |
| `snapkit track kinds` | every kind of upstream, and what each one needs |
| `snapkit import <dir>` | register packaging that already exists |
| `snapkit build <name>` | hand the project to snapcraft |
| `snapkit remove <name>` | forget a snap, and its recipe with it |
| `snapkit db` | what the shared recipe database holds |
| `snapkit db pull [name ...]` | write those projects here, or all of them |
| `snapkit db publish <dir>` | write the database out of the projects here |
| `snapkit install <name>` | fetch it, build it, and offer to install it |

Removing asks first, in both the dashboard and the terminal. It forgets the
record and the recipe stored with it; the project directory is left where it
is, because deleting files you may have edited is a bigger thing than
forgetting a record and should not be what one keystroke does.

A repository can be given any way you have it: `owner/name`, the browser URL,
the clone URL, or a link to a release page.

Useful flags: `--no-build` to write the project without building it, `--tag`
to pin a release, `--asset` to build from a different file in it, `--name` to
call the snap something other than the repository, `--dir` to put the project
somewhere specific, `--plain` to keep the dashboard from opening, and
`--destructive-mode` to let snapcraft build on this host rather than in a
container.

---

[Back to the README](../README.md)
