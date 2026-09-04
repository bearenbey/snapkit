# Tests, and building this

## Tests


```text
./tests.py                    everything that needs no network
./tests.py recipes dashboard  only those groups, by name
./tests.py --online           everything, and the ones that talk to GitHub
```

CI also runs `ruff check`, with the rule set in `pyproject.toml`: pyflakes,
the syntax checks, and bugbear. It is there for the dead branch and the
unused name, which are what a review finds after the fact.

No framework and no dependencies beyond the tool's own, so it runs anywhere
the tool does. The offline tests build their own `.deb` rather than
downloading one, so the archive reader is checked against bytes the test file
made and knows the shape of.

One function per subject: upstreams, architectures, recipes, register,
payloads, projects, checking, dashboard, updater, from_a_file, database,
tracking. A failure names the area before it names the case.

Several exist because of bugs that were in here:

- a build's output was read as text, and Python's universal newlines split on
  the `\r` a progress bar redraws itself with, so every frame of every bar
  became its own line in the log pane and buried everything else
- cancelling a build raised out of the reading loop, and `Popen.__exit__`
  closes the pipe and *waits*: stopping a ten-minute build took ten minutes.
  (The first version of that test raised `KeyboardInterrupt`, which
  `Popen.__exit__` special-cases into giving up on its own, so it passed
  whether or not the child was killed. It raises an ordinary exception now.)

- an asset name split on its separators turned `x86_64` into `x86` and `64`,
  so every 64-bit build read as 32-bit and lost to whatever named no
  architecture at all, which on btop's release page is m68k
- a top-level `icon:` was pointed inside the payload, where snapcraft never
  looks
- every dashboard action set "busy" before asking a guard that refuses
  anything already busy, so none of them ran at all
- a second repository whose name collapsed to one already registered replaced
  it, recipe and all, without a word
- the AppImage recipe globbed for `*.AppImage`, and neovim ships `.appimage`,
  so the build died at `chmod`
- the find-or-add box drew its matches into a header three rows high, so none
  of them appeared
- `check` compared the tag as well as the version, and an imported project
  has no tag, so all sixteen reported an update to the version they were on
- mpv publishes `mpv-v0.41.0-x86_64-w64-mingw32.zip`; `w64` and `mingw32`
  were in neither list, so a Windows build was offered as a Linux one
- writing a project out put its own README over the one that was there, and
  an empty `snapcraft.yaml` into a project that assembles its own tree
- emptying a recipe left its file on disk, so the next read brought the old
  one back and the register disagreed with itself
- the shape a `track` setting is checked against and the shape that reads it
  at update time were two lists that could disagree, so the tracking tests
  put every upstream in `seed.py` back through `configure()` and require it
  to come out unchanged
- `x86_64` was written into the classifier's regex, so on any other machine
  every asset in every release read as built for somewhere else and there was
  nothing left to package; `SNAPKIT_ARCH` lets the tests check eight
  architectures without eight machines
- the terminal and the dashboard each had their own copy of the rule that an
  upstream which resolves to nothing must not be written down, which is the
  one rule here that cannot be got wrong quietly: it now lives in
  `update.retrack` and both are tested against it
- `select()` watches a file descriptor and `sys.stdin` is buffered, so
  reading one character of an arrow key pulled the rest into Python's buffer
  where `select` could not see it; the sequence read as a lone Escape, and
  Escape quit, so every arrow key closed the dashboard
- `summary:` was written into the recipe bare, and GitHub's own description
  of Helium is "Helium: a browser", which YAML reads as a map inside a map
- an update of a recipe with no `http` source found `""` in every line, and
  `replace("", url)` puts the url between every character of it
- `create` wrote the project before asking the register whether the name was
  free, so a second `bat` landed on the first one's directory before it was
  refused; both front ends ask first now
- a .deb's own `Version:` is what the record held, and the tag's version is
  what check compared against, so `1.2.3-1` against `v1.2.3` was an update
  for ever; where both sides have a tag, the tag decides
- the connect was guarded and the read was not, so a download that stalled
  after the connection opened was a bare traceback
- `extractfile` raises for a name the tar does not have, so the fallback to a
  control file stored without `./` never ran and the .deb read as versionless

## Building this


```console
./build.py
sudo snap install --dangerous --classic snapkit_0.2.0_amd64.snap
```

It is a classic snap because building a snap means running snapcraft and
writing project directories wherever you keep them, and a confined snap can
do neither.

## Caveats


- The register scales; the *projects* do not, and that is the thing to watch.
  A thousand registered snaps is a megabyte. A thousand built snaps is
  however large those snaps are, and Electron apps run to a hundred megabytes
  each, all sitting in `projects/`. `snapkit prune` deletes every build but
  the newest; nothing runs it for you.

- The 22 projects in `seed.py` name amd64 asset globs, because that is
  what this machine is. The tool is not tied to it; that list is.
- `grade: devel` on the generated recipes' sibling, because this tool is new.
- A snap built from someone else's release is not published or endorsed by
  them, and every recipe it writes says so in its description.

---

[Back to the README](../README.md)
