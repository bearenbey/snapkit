# The dashboard

Run it with no arguments for the dashboard. Up and down move through the
list, `enter` shows a record in full, `t` says where the selected snap's
releases should be looked for, `?` puts every key on screen, and `q` is the
only thing that quits.

```text
╭──────────────────────────────────────────────────────────────────────────────────╮
│ ◆ SNAPKIT   ▪ 21 registered  ● 18 current  ▲ 2 behind  ◐ 1 in flight             │
╰──────────────────────────────────────────────────────────────────────────────────╯
╭──────────── registered  1-6 of 21 ─────────────╮╭────────── inspector ───────────╮
│     NAME        BUILT      UPSTREAM  STATUS    ││ floorp                         │
│     btop        1.4.7      1.4.7     ● up to…  ││ ▲ UPDATE AVAILABLE             │
│     emacs       30.2       30.2      ● up to…  ││                                │
│ ▸   floorp      12.17.0    13.0.0    ▲ UPDATE  ││ 12.17.0  →  13.0.0             │
│     helium      0.15.6.1   0.15.6.1  ● up to…  ││                                │
│     mpv         0.41.0     0.41.0    ████▊░░░  ││ built    12.17.0               │
│     nvim        0.12.5     0.12.5    ● up to…  ││ kind     archive               │
╰────────────────────────────────────────────────╯╰────────────────────────────────╯
  [↑↓] move  [n] new or find  [r] recheck  [u] update  [b] build  [?] keys  [q] quit
```

`r` asks every upstream what it has now, eight at a time rather than one
after another: twenty-five of them took 29 seconds in a row and take a few
together, and the dashboard runs a check when it opens, so that was most of
what opening it cost. `U` then updates everything the check found behind,
behind one confirmation and with one question about installing at the end
rather than one per snap. `/` narrows the list to what you type, over the
name, repository, summary and kind; `s` puts what needs doing at the top; and
`l` opens the activity log full screen, so the output of a ten-minute build
can be scrolled back through rather than watched go past six lines at a time.

`t` opens the box that says where a snap's releases come from, seeded with
what it tracks now, so changing one word is one word rather than all of them:

```text
╭─────────────────────────────────── track ────────────────────────────────────╮
│ ▸ sublime-text  apt base=https://download.sublimetext.com package=sublime-te…│
│   apt base= package=                       the newest amd64 stanza in an apt…│
│   index url= pattern= asset=               the newest version named in a lis…│
│   redirect url= pattern= asset= download=  the version in the URL a download…│
│   tag-archive repo= asset= download=       a GitHub tag, for a project that …│
│   local                                    the newest package file sitting i…│
│   repo owner/name                          the releases of a github repository│
│   none                                     stop checking it against anything │
╰──────────────────────────────────────────────────────────────────────────────╯
```

It takes the same words `snapkit track` does, and refuses the same way: the
setting is resolved before it is written down, and one that resolves to
nothing leaves the record exactly as it was. The list of kinds is built from
the shapes themselves, so a new one cannot be added and go unmentioned here.

## Building from the dashboard

`b` builds the selected project without the dashboard going anywhere. The
build's own output, ten minutes of snapcraft in most cases, is piped into the
log pane a line at a time rather than being written to the terminal, so
the list, the inspector and the status line keep working throughout and
`q`/Escape still stop the build. Cancelling kills the build rather than
waiting for it to finish.

When it is done, the dashboard asks whether to install what it made:

```text
╭─────────────────────────────── install ────────────────────────────────╮
│ install btop_1.4.7_amd64.snap?  it is not signed, so this installs     │
│ with --dangerous.  [y/N]                                               │
╰────────────────────────────────────────────────────────────────────────╯
```

`y` installs it, anything else does not, and no is the default. Installing is
the one thing here that needs root, so this is the one place that asks for it:
the dashboard steps aside for `sudo snap install --dangerous`, you type your
password on a real terminal, and it comes back when the install is done.
`--classic` is added when the recipe asks for classic confinement. While the
question is up every other key is ignored, so an arrow key cannot answer it by
accident.

Nothing is installed without being asked, and a build that is not installed is
still a build: the `.snap` is in the project directory either way.

Whatever the cursor is on is described beside the list, so reading the
register is moving the cursor rather than opening and closing records. On a
terminal too narrow for two panes the inspector goes and the list takes the
width back, along with the columns the inspector had been showing. The
columns that fit are the columns that are drawn, and the key legend shortens
before it would run off the end.

The box you type into is one box: it searches what you have already packaged
as you type, highlighting the part that matched, and only goes upstream for
something it does not recognise.

```text
▸ find or add  monitor
  ▸ btop                1.4.7             aristocratos/btop
  enter builds the highlighted one from the register -- no download, no lookup
```

---

[Back to the README](../README.md)
