"""Drawing the dashboard."""

from datetime import datetime, timezone

from rich import box
from rich.console import Group
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import sources, update

# Keyed by name, so a new state fails loudly instead of taking a wrong colour.
UPSTREAM_COLOUR = {"current": "green", "behind": "bold yellow",
                   "untracked": "dim", "error": "red"}

# The upstream findings live in update.STATES; the rest are the dashboard's.
STATE_STYLE = {
    "unknown": ("not checked", "dim"),
    "checking": ("checking upstream", "dim"),
    "queued": ("queued", "cyan"),
    "working": ("working", "bold cyan"),
    "done": ("updated", "bold green"),
    "built": ("built", "bold green"),
    "failed": ("failed", "bold red"),
    **{state: (words, UPSTREAM_COLOUR[state])
       for state, words in update.STATES.items()},
}
STATE_GLYPH = {
    "unknown": "·",
    "untracked": "·",
    "checking": "◌",
    "current": "●",
    "behind": "▲",
    "error": "✕",
    "queued": "◌",
    "working": "◐",
    "done": "✔",
    "built": "✔",
    "failed": "✕",
}
KIND_STYLE = {"deb": "magenta", "archive": "cyan", "appimage": "green"}
# Off the shapes themselves, so a kind cannot be added and go unmentioned.
_TRACK_HINTS = tuple(
    [(" ".join([shape.kind] + [k + "=" for k in shape.required
                               if k not in shape.defaults]), shape.summary)
     for shape in sources.SPECS]
    + [("repo owner/name", "the releases of a github repository"),
       ("none", "stop checking it against anything")])
KEYS = (("↑↓", "move"), ("n", "new or find"), ("r", "recheck"), ("u", "update"),
        ("b", "build"), ("/", "filter"), ("l", "log"), ("?", "keys"),
        ("q", "quit"))
SHORT_KEYS = (("↑↓", "move"), ("n", "find"), ("r", "recheck"), ("u", "update"),
              ("b", "build"), ("?", "keys"), ("q", "quit"))

# Every key the dashboard answers to, which the footer has never had room for.
HELP = (
    ("moving about", (
        ("↑ ↓  j k", "move"),
        ("PgUp PgDn", "a screenful"),
        ("home  G", "the first, the last"),
        ("/", "narrow the list to what you type; escape clears it"),
        ("s", "order by the register, or by what needs doing"),
    )),
    ("one snap, the one under the cursor", (
        ("enter", "its record, in full"),
        ("u", "move it onto the release the last check found"),
        ("b", "hand the project to snapcraft as it stands"),
        ("t", "where its releases should be looked for"),
        ("d", "forget it, after asking"),
    )),
    ("the whole register", (
        ("n", "find something registered, or add something new"),
        ("r", "ask every upstream what it has now"),
        ("U", "update everything a check found behind"),
        ("g", "read the shared database and offer what is missing"),
    )),
    ("while something is running", (
        ("q  escape", "stop it"),
        ("l", "the activity log, full screen and scrollable"),
        ("y  n", "answer the question on screen"),
    )),
)


def advertised():
    """Every key the dashboard tells a person about, footer and keys page."""
    keys = {key for key, _ in KEYS} | {key for key, _ in SHORT_KEYS}
    for _, pairs in HELP:
        for key, _ in pairs:
            keys.update(key.split())
    return keys


ACCENT = "#4ce0ff"          # the one colour that means "this, here"
EDGE = "grey42"             # panel borders, which should be seen and not read
CURSOR_ROW = "on grey15"
WORDMARK = "◆ SNAPKIT"
SPINNER = "◐◓◑◒"
PARTIAL = ("", "▏", "▎", "▍", "▌", "▋", "▊", "▉")
LOG_HEIGHT = 8
INSPECTOR = 40              # columns the inspector takes when there is room
SPLIT_AT = 108              # ... and the width below which there is not


class Screen:
    """One dashboard, drawn."""

    def __init__(self, board):
        self.board = board
        self.frame = 0
        self.window = 1         # rows that fit; the first render works it out
        self.height = 30        # ... and the whole screen, for a full-page view
        self.offset = 0

    def render(self):
        """One frame."""
        self.frame += 1
        console = self.board.live.console if self.board.live else None
        height = console.size.height if console else 30
        width = console.size.width if console else 100
        self.height = height
        self.window = max(1, height - self._header_height() - LOG_HEIGHT - 4)

        # Any of these is the whole screen while it is up.
        if self.board.detail is not None:
            return self._details()
        if self.board.picking is not None:
            return self._picker()
        if self.board.helping:
            return self._help()
        if self.board.reading_log:
            return self._logbook()

        body = Layout(name="body")
        if width >= SPLIT_AT and self.board.rows:
            body.split_row(
                Layout(self._table(width - INSPECTOR, compact=True), name="table"),
                Layout(self._inspector(), name="inspector", size=INSPECTOR))
        else:
            body.update(self._table(width))

        layout = Layout()
        layout.split_column(
            Layout(self._header(width), size=self._header_height()),
            body,
            Layout(self._log(), size=LOG_HEIGHT),
            Layout(self._footer(width), size=1))
        return layout
    def _header_height(self):
        """Tall enough for what the header has to say."""
        if self.board.tracking:
            return 2 + 1 + len(_TRACK_HINTS)
        if not self.board.prompting:
            return 3
        return 2 + 1 + min(len(self.board.matches), 5) + 1
    def _header(self, width=100):
        if self.board.asking:
            return self._asking_header()
        if self.board.confirm:
            return self._confirm_header()
        if self.board.tracking:
            return self._track_header(width)
        if self.board.prompting:
            return self._prompt_header()
        return Panel(Group(self._masthead()), box=box.ROUNDED,
                     border_style=EDGE, padding=(0, 1))
    def _masthead(self):
        """The wordmark, and the shape of the register beside it."""
        # Of the register, not of what a filter happens to be showing.
        counts = {}
        for row in self.board.known:
            counts[row.state] = counts.get(row.state, 0) + 1
        behind = counts.get("behind", 0)
        current = counts.get("current", 0)
        busy = sum(counts.get(s, 0) for s in ("queued", "working", "checking"))
        failed = counts.get("failed", 0) + counts.get("error", 0)

        line = Text()
        line.append_text(_gradient(WORDMARK))
        line.append("   ")
        line.append_text(_chip("▪", len(self.board.known), "registered", "cyan"))
        if current:
            line.append("  ")
            line.append_text(_chip("●", current, "current", "green"))
        if behind:
            line.append("  ")
            line.append_text(_chip("▲", behind, "behind", "yellow"))
        if busy:
            line.append("  ")
            line.append_text(_chip(_spinner(self.frame), busy, "in flight", "cyan"))
        if failed:
            line.append("  ")
            line.append_text(_chip("✕", failed, "failed", "red"))
        if self.board.status:
            line.append("   ")
            line.append(self.board.status, style="dim")
        return line
    def _asking_header(self):
        return Panel(
            Text.assemble(
                (self.board.asking, "bold"),
                ("  it is not signed, so this installs with --dangerous.  ",
                 "dim"),
                ("[y/N]", "bold " + ACCENT)),
            title="install", box=box.ROUNDED, border_style=ACCENT,
            padding=(0, 1))

    def _confirm_header(self):
        return Panel(
            Text.assemble(
                ("forget ", "bold"), (self.board.confirm, "bold yellow"),
                ("?  its record and the snapcraft.yaml stored with it go too. "
                 "The project directory stays.  ", "bold"),
                ("[y/N]", "bold red")),
            title="delete", box=box.ROUNDED, border_style="red", padding=(0, 1))
    def _track_header(self, width=100):
        """Typing where one snap's releases should be looked for."""
        caret = "▌" if self.frame // 6 % 2 else " "
        # One line, whatever is typed: a wrapped header is a header of the
        # wrong height, and these specs run to a couple of hundred characters.
        room = max(20, width - len(self.board.tracking) - 10)
        typed = self.board.prompt
        if len(typed) > room:
            typed = "…" + typed[-(room - 1):]
        lines = [Text.assemble(("▸ ", ACCENT),
                               (f"{self.board.tracking}  ", "bold"),
                               (typed, "bold " + ACCENT), (caret, ACCENT))]
        column = max(len(form) for form, _ in _TRACK_HINTS) + 2
        column = min(column, max(12, width - 20))
        room = width - 6 - column
        for form, what in _TRACK_HINTS:
            line = Text("  ")
            line.append(f"{_fit(form, column - 1):<{column}}",
                        style="dim " + ACCENT)
            if room >= 12:
                line.append(_fit(what, room), style="dim")
            lines.append(line)
        return Panel(Group(*lines), title="track", box=box.ROUNDED,
                     border_style=ACCENT, padding=(0, 1))

    def _prompt_header(self):
        caret = "▌" if self.frame // 6 % 2 else " "
        body = Text.assemble(("▸ ", ACCENT), ("find or add  ", "dim"),
                             (self.board.prompt, "bold " + ACCENT), (caret, ACCENT))
        lines = [body]
        for index, snap in enumerate(self.board.matches[:5]):
            here = index == self.board.match_cursor
            line = Text("  ")
            line.append("▸ " if here else "  ", style=ACCENT if here else "")
            line.append_text(_highlight(snap.name, self.board.prompt,
                                        "bold green" if here else "bold"))
            line.append(" " * max(1, 20 - len(snap.name)))
            line.append(f"{snap.version:<18} ", style="dim")
            line.append(snap.repo, style="dim")
            lines.append(line)
        if self.board.matches:
            lines.append(Text("  enter builds the highlighted one from the "
                              "register -- no download, no lookup", style="dim"))
        elif self.board.prompt and local.looks_like_path(self.board.prompt):
            lines.append(Text("  nothing registered matches; enter packages "
                              "what is at that path", style="dim"))
        elif self.board.prompt:
            lines.append(Text("  nothing registered matches; enter makes a new "
                              "one from this repository", style="dim"))
        else:
            lines.append(Text("  a name you have used before, a github url, or "
                              "the path to a .deb, an archive or a folder",
                              style="dim"))
        return Panel(Group(*lines), title="find or add", box=box.ROUNDED,
                     border_style=ACCENT, padding=(0, 1))
    def _table(self, width=100, compact=False):
        """The registered snaps, scrolled so the cursor is always on screen."""
        first, last = self._window()
        table = Table(expand=True, box=None, pad_edge=False,
                      header_style="dim " + EDGE)
        table.add_column(" ", width=2)
        table.add_column("NAME", width=18, no_wrap=True)
        table.add_column("BUILT", width=15, no_wrap=True)
        table.add_column("UPSTREAM", width=15, no_wrap=True)
        table.add_column("STATUS", width=22, no_wrap=True)

        # Optional columns go in while there is room; squeezed is worse.
        room = width - 2 - (2 + 18 + 15 + 15 + 22) - 2 * 5
        extra = []
        if not compact:
            for heading, size in (("KIND", 9), ("AGE", 5), ("REPOSITORY", 24)):
                if room >= size + 2:
                    extra.append(heading)
                    room -= size + 2
                    if heading == "REPOSITORY":
                        table.add_column(heading, overflow="ellipsis")
                    else:
                        table.add_column(heading, width=size, no_wrap=True,
                                         justify="right" if heading == "AGE" else "left")

        for index in range(first, last):
            row = self.board.rows[index]
            here = index == self.board.cursor
            cells = [
                Text("▸" if here else " ", style="bold " + ACCENT),
                Text(row.name, style="bold" if here else ""),
                Text(row.snap.version or "-"),
                _upstream_cell(row),
                self._status_cell(row),
            ]
            for heading in extra:
                if heading == "KIND":
                    cells.append(_kind_badge(row.snap.kind))
                elif heading == "AGE":
                    cells.append(Text(_ago(row.snap.updated), style="dim"))
                else:
                    cells.append(Text(
                        row.note or row.snap.repo or "(no upstream recorded)",
                        style="" if row.note else "dim"))
            table.add_row(*cells, style=CURSOR_ROW if here else "")

        if not self.board.rows:
            empty = [Text(""), Text("nothing here yet -- press n", style="dim")]
            table.add_row(*(empty + [Text("")] * (len(table.columns) - 2)))

        title = "registered"
        if len(self.board.rows) > last - first:
            title = f"registered  {first + 1}-{last} of {len(self.board.rows)}"
        if self.board.needle:
            caret = "|" if self.board.filtering and self.frame // 6 % 2 else ""
            title += f"   /{self.board.needle}{caret}"
            hidden = len(self.board.known) - len(self.board.rows)
            if hidden:
                title += f"  ({hidden} hidden)"
        elif self.board.filtering:
            title += "   /"
        if self.board.order == "attention":
            title += "   by attention"
        return Panel(table, title=title, box=box.ROUNDED, border_style=EDGE,
                     padding=(0, 1))
    def _status_cell(self, row):
        """What this snap is doing, as one cell."""
        if row.state == "working" and row.total_bytes:
            share = row.done_bytes / row.total_bytes
            return Text.assemble(
                (_smooth_bar(share, 12), ACCENT),
                (f" {share * 100:3.0f}%", "dim"))
        label, style = STATE_STYLE.get(row.state, (row.state, ""))
        glyph = STATE_GLYPH.get(row.state, "·")
        if row.state in ("working", "queued", "checking"):
            glyph = _spinner(self.frame)
        return Text.assemble((glyph + " ", style), (label, style))
    def _window(self):
        """Which rows to draw, following the cursor."""
        total = len(self.board.rows)
        height = max(1, self.window)
        if total <= height:
            self.offset = 0
            return 0, total
        if self.board.cursor < self.offset:
            self.offset = self.board.cursor
        elif self.board.cursor >= self.offset + height:
            self.offset = self.board.cursor - height + 1
        self.offset = max(0, min(self.offset, total - height))
        return self.offset, self.offset + height
    def _inspector(self):
        """Everything about the highlighted snap that fits without asking."""
        row = self.board.row
        if row is None:
            return Panel(Text("nothing selected", style="dim"),
                         title="inspector", box=box.ROUNDED,
                         border_style=EDGE, padding=(0, 1))
        snap = row.snap
        label, style = STATE_STYLE.get(row.state, (row.state, ""))
        glyph = STATE_GLYPH.get(row.state, "·")

        lines = [_gradient(snap.name),
                 Text.assemble((glyph + " ", style), (label, style)),
                 Text("")]

        if row.state == "behind" and row.latest:
            lines.append(Text.assemble(
                (f"{snap.version or '-'}", "dim"), ("  →  ", ACCENT),
                (row.latest, "bold yellow")))
            lines.append(Text(""))

        # A table, not spacing: wrapped text must land under the value.
        facts = Table(box=None, pad_edge=False, show_header=False, padding=0)
        facts.add_column(style="dim", width=9, no_wrap=True)
        facts.add_column(overflow="fold")
        facts.add_row("built", snap.version or "-")
        facts.add_row("upstream", row.latest or "-")
        facts.add_row("kind", _kind_badge(snap.kind))
        facts.add_row("source", _source_of(snap))
        if snap.asset:
            facts.add_row("asset", snap.asset)
        facts.add_row("builds", str(snap.builds or len(snap.history)))
        if snap.updated:
            facts.add_row("updated", _when(snap.updated))
        if snap.plugs:
            chips = Text()
            for plug in snap.plugs[:6]:
                chips.append(plug + " ", style="dim " + ACCENT)
            if len(snap.plugs) > 6:
                chips.append(f"+{len(snap.plugs) - 6} more", style="dim")
            facts.add_row("plugs", chips)
        lines.append(facts)

        trail = _lineage(snap)
        if trail is not None:
            lines += [Text(""), trail]

        if snap.summary:
            lines += [Text(""), Text(snap.summary, style="grey70")]

        return Panel(Group(*lines), title="inspector", box=box.ROUNDED,
                     border_style=EDGE, padding=(0, 1))
    def _logbook(self):
        """The activity log, full screen, scrolled back to where it was left."""
        lines = list(self.board.log)
        room = max(1, self.height - 4)
        end = len(lines) - self.board.log_offset
        shown = lines[max(0, end - room):end]
        where = ("newest" if not self.board.log_offset
                 else f"{self.board.log_offset} line"
                      f"{'' if self.board.log_offset == 1 else 's'} back")
        return Panel(Group(*shown) if shown else Text("nothing yet", style="dim"),
                     title=f"activity -- {len(lines)} kept, {where}",
                     subtitle="↑↓ PgUp PgDn   home oldest   G newest   q closes",
                     box=box.ROUNDED, border_style=EDGE, padding=(0, 1))

    def _help(self):
        """Every key, grouped, because the footer only ever fits a few."""
        lines = []
        for heading, keys in HELP:
            if lines:
                lines.append(Text(""))
            lines.append(Text(f"  {heading}", style="bold " + ACCENT))
            for key, what in keys:
                lines.append(Text.assemble(("    ", ""), (f"{key:<12}", "bold"),
                                           (what, "dim")))
        return Panel(Group(*lines), title="keys", subtitle="any key closes",
                     box=box.ROUNDED, border_style=EDGE, padding=(1, 1))

    def _log(self):
        lines = list(self.board.log)[-(LOG_HEIGHT - 2):]
        return Panel(Group(*lines) if lines else Text(""),
                     title="activity", box=box.ROUNDED,
                     border_style="grey35", padding=(0, 1))
    def _footer(self, width=100):
        if self.board.busy:
            return Text.assemble(("  ", ""), (_spinner(self.frame), ACCENT),
                                 (f" {self.board.busy}", "bold " + ACCENT),
                                 ("   q or escape to stop", "dim"))
        # Shrink the legend rather than let it run off the right of the screen.
        for legend in (KEYS, SHORT_KEYS):
            keys = _keys(legend)
            if len(keys.plain) <= width:
                return keys
        return _keys(tuple((key, "") for key, _ in SHORT_KEYS))
    def _picker(self):
        """The ranking, with the reason each file scored what it did."""
        table = Table(box=None, pad_edge=False, expand=True,
                      header_style="dim " + EDGE)
        table.add_column(" ", width=2)
        table.add_column("#", width=3)
        table.add_column("FILE", width=46, overflow="ellipsis")
        table.add_column("KIND", width=10)
        table.add_column("WHY", overflow="ellipsis")
        for index, candidate in enumerate(self.board.picking.candidates):
            here = index == self.board.pick_cursor
            table.add_row(Text("▸" if here else " ", style="bold " + ACCENT),
                          str(index + 1), candidate.name,
                          _kind_badge(candidate.kind), candidate.why,
                          style=CURSOR_ROW if here else "")
        hint = _keys((("↑↓", "choose"), ("1-9", "pick"), ("enter", "build it"),
                      ("esc", "give up")))
        return Panel(Group(table, Text(""), hint),
                     title=f"{self.board.picking.title} -- which file?",
                     box=box.ROUNDED, border_style=ACCENT, padding=(0, 1))
    def _details(self):
        snap = self.board.detail
        rows = Table(box=None, pad_edge=False)
        rows.add_column(style="dim", width=16)
        rows.add_column(overflow="fold")
        for key in ("repo", "kind", "version", "tag", "asset", "asset_pattern",
                    "command", "icon", "license", "directory", "created", "updated"):
            rows.add_row(key, str(getattr(snap, key, "") or "-"))
        rows.add_row("plugs", ", ".join(snap.plugs) or "-")
        rows.add_row("builds", str(snap.builds or len(snap.history)))
        head = Group(_gradient(snap.name), rows)
        yaml = Text(snap.snapcraft_yaml or "(none)", style="grey70")
        return Panel(Group(head, Text(""), yaml),
                     title="any key to go back", box=box.ROUNDED,
                     border_style=ACCENT, padding=(0, 1))

def _gradient(text, start=(0x4C, 0xE0, 0xFF), end=(0xB9, 0x8C, 0xFF)):
    """The wordmark and every snap name, cyan to violet across the letters."""
    out = Text()
    span = max(1, len(text) - 1)
    for index, character in enumerate(text):
        mix = index / span
        shade = tuple(int(a + (b - a) * mix) for a, b in zip(start, end))
        out.append(character, style="bold #%02x%02x%02x" % shade)
    return out
def _fit(text, room):
    """`text`, shortened to `room` columns, so a header cannot wrap."""
    return text if len(text) <= room else text[:max(1, room - 1)] + "…"


def _chip(glyph, count, label, style):
    return Text.assemble((glyph + " ", style), (str(count), "bold " + style),
                         (" " + label, "dim"))
def _keys(pairs):
    out = Text("  ")
    for key, label in pairs:
        out.append("[", style="dim " + ACCENT)
        out.append(key, style="bold " + ACCENT)
        out.append("] ", style="dim " + ACCENT)
        out.append(label + "   ", style="dim")
    return out
def _spinner(frame):
    return SPINNER[(frame // 3) % len(SPINNER)]
def _highlight(text, needle, base):
    """`text` with the part that matched picked out of it."""
    out = Text()
    where = text.lower().find(needle.lower()) if needle else -1
    if where < 0:
        out.append(text, style=base)
        return out
    out.append(text[:where], style=base)
    out.append(text[where:where + len(needle)], style="bold " + ACCENT)
    out.append(text[where + len(needle):], style=base)
    return out
def _kind_badge(kind):
    return Text(kind or "-", style=KIND_STYLE.get(kind, "dim"))
def _source_of(snap):
    """Where this snap's releases come from, for the inspector."""
    text = sources.label(snap)
    return Text(text) if text else Text("(none recorded)", style="dim")


def _upstream_cell(row):
    if not row.latest:
        return Text("-", style="dim")
    style = "bold yellow" if row.behind else "dim"
    return Text(row.latest, style=style)
def _lineage(snap):
    """The last few versions this snap has been on, oldest first."""
    seen = []
    for entry in snap.history:
        version = entry.get("version") if isinstance(entry, dict) else None
        if version and version not in seen:
            seen.append(version)
    if len(seen) < 2:
        return None
    trail = Text.assemble((f"{'history':<9}", "dim"))
    for index, version in enumerate(seen[-4:]):
        if index:
            trail.append(" → ", style="dim " + ACCENT)
        trail.append(version, style="dim")
    return trail
def _ago(stamp):
    """How long ago, in as few characters as it takes."""
    when = _parse(stamp)
    if when is None:
        return ""
    seconds = max(0, (datetime.now(timezone.utc) - when).total_seconds())
    for cut, size, suffix in ((90, 1, "s"), (5400, 60, "m"),
                              (172800, 3600, "h"), (1209600, 86400, "d"),
                              (7889400, 604800, "w")):
        if seconds < cut:
            return "now" if size == 1 else f"{int(seconds // size)}{suffix}"
    return f"{int(seconds // 2629800)}mo"
def _when(stamp):
    """The date, with how long ago it was after it."""
    when = _parse(stamp)
    if when is None:
        return stamp or ""
    return f"{when.date()}  ({_ago(stamp)} ago)"
def _parse(stamp):
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
def _smooth_bar(share, width=12):
    """A bar that moves an eighth of a cell at a time."""
    share = min(1.0, max(0.0, share))
    exact = share * width
    whole = int(exact)
    bar = "█" * whole
    if whole < width:
        bar += PARTIAL[int((exact - whole) * 8)]
    return bar.ljust(width, "░")[:width]
