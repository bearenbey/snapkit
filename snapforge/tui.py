"""The dashboard: its state, its keys, and the one worker thread."""

import subprocess
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass, field

from rich.live import Live
from rich.text import Text

from . import adopt, github, local, project, snapdb, sources, update
from .keys import Keyboard
from .db import NameTaken
from .net import NetworkError
from .report import Reporter
from .screen import Screen

REFRESH = 12


class Cancelled(Exception):
    """The person asked for the work in flight to stop."""


@dataclass
class Row:
    snap: object
    state: str = "unknown"
    note: str = ""
    latest: str = ""
    release: object = None
    asset: object = None
    done_bytes: int = 0
    total_bytes: int = 0

    @property
    def name(self):
        return self.snap.name

    @property
    def behind(self):
        return self.state == "behind"

    def matches(self, needle):
        """Whether this row is one a filter of `needle` keeps."""
        if not needle:
            return True
        snap = self.snap
        return any(needle in (field or "").lower() for field in
                   (snap.name, snap.repo, snap.summary, snap.kind))


def edited(text, key, lower=False):
    """`text` with this keystroke applied, or None when it was not typing."""
    if key == "backspace":
        return text[:-1]
    if key and len(key) == 1 and key.isprintable():
        return text + (key.lower() if lower else key)
    return None


# What needs a person, first, when the list is ordered by attention.
ATTENTION = {"behind": 0, "failed": 1, "error": 2, "untracked": 3}

# The modal states, in the order they take the keyboard from one another.
MODES = ("asking", "tracking", "prompting", "picking", "confirm", "detail",
         "helping", "reading_log", "filtering")


@dataclass
class Dashboard:
    """Everything on screen, and the one thread allowed to change it."""

    db: object
    known: list = field(default_factory=list)     # a Row for every record
    rows: list = field(default_factory=list)      # those the list is showing
    cursor: int = 0
    busy: str = ""
    status: str = ""
    prompt: str = ""          # what is being typed, "" when not typing
    prompting: bool = False
    tracking: str = ""        # a snap whose upstream is being typed, or ""
    confirm: str = ""         # a snap awaiting a yes before it is forgotten
    picking: object = None    # a Plan waiting for its asset to be chosen
    pick_cursor: int = 0
    asking: str = ""          # a yes/no the worker is blocked on
    matches: list = field(default_factory=list)   # what the prompt found
    match_cursor: int = 0
    detail: object = None     # a Snap being looked at, or None
    reading_log: bool = False  # the activity log, full screen
    log_offset: int = 0        # lines back from the newest, 0 at the tail
    filtering: bool = False    # a filter is being typed
    needle: str = ""           # what the list is narrowed to, "" for all
    order: str = "register"    # or "attention": what needs doing, first
    helping: bool = False      # the key list, full screen
    quit: bool = False
    live = None               # rich's Live, once run_dashboard has one
    # Scroll, height and frame live on self.screen: drawing, not register.

    def __post_init__(self):
        self.screen = Screen(self)
        self.log = deque(maxlen=400)
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.picked = threading.Event()
        self.answered = threading.Event()
        self.answer = False
        self.worker = None
        self.keyboard = None
        self.reload()
        for record, why in getattr(self.db, "problems", []):
            self.say(f"{record.name} could not be read and was left out: {why}",
                     "bold red")
        self.status = (f"{len(self.known)} registered -- press n to make "
                       f"one from a github repository" if self.known else
                       "nothing registered yet -- press n and paste a github url")

    # -- state ---------------------------------------------------------------

    def reload(self):
        with self.lock:
            keep = {r.name: r for r in self.known}
            self.known = [keep.get(s.name) or Row(snap=s)
                          for s in self.db.all()]
            for row in self.known:
                row.snap = self.db.snaps.get(row.name, row.snap)
            self.restack()

    def restack(self):
        """What the list shows: every record, narrowed and ordered.

        `known` is the register. `rows` is the view of it, so a filter hides
        rows from the eye without hiding them from `r` or `U`.
        """
        with self.lock:
            here = self.row.name if self.row else ""
            rows = [row for row in self.known if row.matches(self.needle)]
            if self.order == "attention":
                rows.sort(key=lambda row: (ATTENTION.get(row.state, 4),
                                           row.name))
            self.rows = rows
            self.cursor = min(self.cursor, max(0, len(rows) - 1))
            if here:
                self.select(here)

    @property
    def row(self):
        return self.rows[self.cursor] if self.rows else None

    def move(self, by):
        """Move the cursor, and keep it inside the list."""
        if not self.rows:
            return
        self.cursor = max(0, min(self.cursor + by, len(self.rows) - 1))

    def say(self, text, style=""):
        with self.lock:
            self.log.append(Text(text, style=style) if style else Text(text))

    def idle(self):
        """Back to saying what the register holds, with nothing in flight."""
        self.status = f"{len(self.known)} registered"

    @property
    def mode(self):
        """Whichever modal state is up, or "" for the list itself.

        Both the keys and the drawing read this, so a new one cannot be
        added to the board and go unhandled by either of them.
        """
        return next((name for name in MODES if getattr(self, name)), "")

    # -- work ----------------------------------------------------------------

    def run_job(self, label, function, *args):
        """Start a piece of work on the worker thread."""
        if self.busy:
            self.say(f"busy {self.busy}; wait for that to finish", "yellow")
            return False
        self.cancel.clear()
        self.busy = label

        def wrapper():
            try:
                function(*args)
            except Cancelled:
                self.say("cancelled", "yellow")
            except Exception as exc:                      # noqa: BLE001
                # A silent death leaves the dashboard looking fine and idle.
                self.say(f"{type(exc).__name__}: {exc}", "bold red")
                self.status = "something went wrong -- see the log"
            finally:
                self.busy = ""

        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()
        return True

    def look_at(self, row):
        """Ask one upstream what it has, and put the answer on its row."""
        if self.cancel.is_set():
            row.state = "unknown"
            return
        try:
            # Skipping on `repo` missed every non-GitHub upstream.
            found = update.situation(row.snap)
        except Exception as exc:                          # noqa: BLE001
            # One unreadable record must not take the other twenty-four down.
            row.state, row.note = "error", f"{type(exc).__name__}: {exc}"
            self.say(f"{row.name}: {row.note}", "red")
            return
        row.state = found.state
        row.release, row.asset = found.release, found.asset
        row.latest = found.latest
        row.note = found.note or found.problem
        if found.note:
            self.say(f"{row.name}: {found.note}", "yellow")
        if found.state == "error":
            self.say(f"{row.name}: {found.problem}", "red")

    def recheck(self):
        """Ask every registered repository what it has now."""
        if not self.known:
            return
        rows = list(self.known)

        def work():
            for row in rows:
                row.state, row.note = "checking", ""
            done = 0
            self.status = f"checking {len(rows)} upstream" + \
                          ("" if len(rows) == 1 else "s")
            with ThreadPoolExecutor(min(update.AT_ONCE, len(rows))) as pool:
                waiting = [pool.submit(self.look_at, row) for row in rows]
                for finished in as_completed(waiting):
                    finished.result()
                    done += 1
                    self.status = f"checked {done} of {len(rows)}"
            behind = sum(1 for r in self.known if r.behind)
            self.status = (f"{behind} to update" if behind
                           else "everything is up to date")

        self.run_job("checking", work)

    def create(self, text):
        """Make a snap from a repository, or from a package file on disk."""
        def work():
            reporter = DashboardReporter(self, None)
            self.status = f"creating from {text}"
            if local.looks_like_path(text):
                made = self._plan_local(text, reporter)
                if made is None:
                    return
            else:
                repo = github.parse_repo(text)
                known = self.db.find_repo(repo)
                if known:
                    self.say(f"{repo} is already registered as {known.name} -- "
                             f"select it and press u to update it", "yellow")
                    self.select(known.name)
                    self.idle()
                    return
                made = project.plan(text, reporter)
            if len(made.candidates) > 1:
                made.chosen = self._ask_which(made)
                reporter.detail(f"building from {made.chosen.name}")
            snap = project.create(made, reporter)
            try:
                self.db.add(snap)
            except NameTaken as exc:
                # The project is written; a free name is one keystroke away.
                free = self.db.free_name(snap.name)
                self.say(str(exc), "red")
                self.say(f"registering it as {free} instead", "yellow")
                snap.name = free
                snap.directory = ""
                project.write(snap, reporter)
                self.db.add(snap)
            self.reload()
            self.select(snap.name)
            self.say(f"registered {snap.name} {snap.version}", "bold green")
            row = self.row_for(snap.name)
            if row:
                row.state, row.latest = "current", snap.version
            self._build(snap, reporter, row)

        self.run_job("creating", work)

    def _plan_local(self, text, reporter):
        """A file or a folder that was typed into the box instead of a repo."""
        directory = Path(text).expanduser()
        directory = directory if directory.is_dir() else directory.parent
        for snap in self.db:
            if snap.directory and Path(snap.directory).resolve() == directory.resolve():
                self.say(f"{directory} is {snap.name} -- select it and press u "
                         f"to pick up what is in there", "yellow")
                self.select(snap.name)
                self.idle()
                return None
        return project.plan_local(text, reporter)

    def _ask_which(self, made):
        """Put the ranking on screen and wait for a person to pick one."""
        self.picking, self.pick_cursor = made, 0
        self.picked.clear()
        self.status = "which file should this be built from?"
        try:
            while not self.picked.wait(0.1):
                if self.cancel.is_set():
                    raise Cancelled()
            if self.pick_cursor < 0:
                raise Cancelled()
            return made.candidates[self.pick_cursor]
        finally:
            self.picking = None

    def pull_database(self):
        """Offer to write every snap the database has and this does not."""
        def work():
            reporter = DashboardReporter(self, None)
            self.status = "reading the database"
            found = snapdb.index()
            snaps = found["snaps"]
            new = sorted(n for n in snaps if n not in self.db.snaps)
            if not new:
                self.say(f"the database has {len(snaps)} snaps, all registered "
                         f"here already", "dim")
                return

            self.say(f"the database has {len(new)} not registered here: "
                     f"{', '.join(new[:8])}" + (" ..." if len(new) > 8 else ""))
            if not self._ask_yes_no(f"write {len(new)} projects from the database?"):
                self.say("left the database alone", "dim")
                return

            # Beside the projects already here, not wherever this was started.
            where = Path(next(iter(self.db.all())).path).parent \
                if self.db.snaps else Path.cwd()
            taken = 0
            for name in new:
                target = where / f"{name}-snap"
                self.status = f"fetching {name}"
                try:
                    snapdb.fetch(name, target, found, reporter=reporter)
                    snap, _, _, _ = adopt.read(target)
                    snapdb.apply_record(snap, snaps[name])
                    self.db.add(snap, replace=True)
                    taken += 1
                except (snapdb.DatabaseError, adopt.NotAProject) as exc:
                    self.say(f"{name}: {exc}", "yellow")
            self.reload()
            self.say(f"wrote {taken} of {len(new)} into {where}", "bold green")
            self.idle()

        self.run_job("pulling", work)

    def track(self, name, text):
        """Point a snap at another upstream, typed as `kind key=value`."""
        snap = self.db.snaps.get(name)
        words = text.split()
        if snap is None or not words:
            # An emptied box is "never mind". Untracking is asked for by
            # name; a stray return key must not throw an upstream away.
            return

        def work():
            row = self.row_for(name)
            if words[0] in ("none", "off"):
                update.untrack(snap)
                self.db.add(snap, replace=True)
                self.say(f"{name} is not tracked against anything now",
                         "yellow")
                if row:
                    row.state, row.latest, row.note = "untracked", "", ""
                return

            kind = "local" if words[0] == "folder" else words[0]
            wanted = sources.configure(kind, sources.parse_pairs(words[1:]))
            self.status = f"resolving {sources.summarise(wanted)}"
            try:
                release = update.retrack(snap, wanted)
            except (NetworkError, project.ForgeError) as exc:
                # Written down untried, a wrong setting reads as up to date.
                self.say(f"{name} was left as it was: {exc}", "red")
                self.idle()
                return

            self.db.add(snap, replace=True)
            for note in update.fitting(snap, release):
                self.say(f"{name}: {note}", "yellow")
            self.say(f"{name} is tracked against "
                     f"{sources.label(snap, folder='its own folder')} -- "
                     f"upstream has {release.version}", "bold green")
            if row:
                found = update.situation(snap)
                row.state, row.latest = found.state, found.latest
                row.release, row.asset = found.release, found.asset
                row.note = found.note or found.problem
            self.idle()

        self.run_job("tracking", work)

    def _ask_yes_no(self, question):
        """Put a question on screen and block the worker until answered."""
        self.asking = question
        self.answered.clear()
        try:
            while not self.answered.wait(0.1):
                if self.cancel.is_set():
                    return False
            return self.answer
        finally:
            self.asking = ""

    def _install(self, snap, built):
        """Install what was just built, with the terminal handed back."""
        classic = ""
        try:
            recipe = (Path(snap.path) / "snap" / "snapcraft.yaml").read_text()
            if "confinement: classic" in recipe:
                classic = " --classic"
        except OSError:
            pass

        command = f"sudo snap install --dangerous{classic} {built}"
        self.say(f"installing: {command}", "cyan")
        with self.suspended():
            print(f"\n{command}\n", flush=True)
            done = subprocess.run(["sudo", "snap", "install", "--dangerous",
                                   *(["--classic"] if classic else []),
                                   str(built)])
        if done.returncode == 0:
            self.say(f"installed {snap.name}", "bold green")
        else:
            self.say(f"install exited with status {done.returncode}", "red")

    def package(self, snap):
        """Build something already registered, without going upstream."""
        def work():
            reporter = DashboardReporter(self, self.row_for(snap.name))
            self.select(snap.name)
            row = self.row_for(snap.name)
            if row:
                row.state = "working"
            self.status = f"packaging {snap.name} from the register"
            project.package(snap, reporter, build_it=False)
            self._build(snap, reporter, row)
            self.idle()

        self.run_job("packaging", work)

    def update_selected(self):
        """Move the selected snap onto the release found by the last check."""
        row = self.row
        if row is None:
            return
        if row.state != "behind":
            self.say(f"{row.name} has nothing to update -- press r to check",
                     "yellow")
            return

        def work():
            reporter = DashboardReporter(self, row)
            row.state = "working"
            self.status = f"updating {row.name}"
            try:
                project.adopt(row.snap, reporter)
                update.update(row.snap, row.release, row.asset, reporter)
                self.db.add(row.snap)
                row.latest = row.snap.version
                row.state = "done"
                self._build(row.snap, reporter, row)
            except (NetworkError, project.ForgeError) as exc:
                row.state, row.note = "failed", str(exc)
                self.say(f"{row.name}: {exc}", "red")
            except Cancelled:
                row.state = "behind"
                raise
            finally:
                row.done_bytes = row.total_bytes = 0
                self.status = "done -- press r to check again"

        self.run_job("updating", work)

    def update_all(self):
        """Update everything the last check found behind, one after another."""
        behind = [row for row in self.known if row.behind]
        if not behind:
            self.say("nothing is behind -- press r to check", "yellow")
            return

        def work():
            many = "" if len(behind) == 1 else "s"
            if not self._ask_yes_no(f"update {len(behind)} snap{many}?"):
                self.say("left them alone", "dim")
                return
            for row in behind:
                row.state = "queued"
            built = []
            for index, row in enumerate(behind, 1):
                if self.cancel.is_set():
                    row.state = "behind"
                    continue
                reporter = DashboardReporter(self, row)
                self.select(row.name)
                row.state = "working"
                self.status = f"updating {row.name} ({index} of {len(behind)})"
                try:
                    project.adopt(row.snap, reporter)
                    update.update(row.snap, row.release, row.asset, reporter)
                    self.db.add(row.snap)
                    row.latest = row.snap.version
                    made = self._build(row.snap, reporter, row,
                                       ask_install=False)
                    if made is not None:
                        built.append((row.snap, made))
                except (NetworkError, project.ForgeError) as exc:
                    row.state, row.note = "failed", str(exc)
                    self.say(f"{row.name}: {exc}", "red")
                except Cancelled:
                    row.state = "behind"
                    raise
                finally:
                    row.done_bytes = row.total_bytes = 0

            self.status = f"built {len(built)} of {len(behind)}"
            # One question for the run, rather than one for every snap in it.
            if built:
                many = "" if len(built) == 1 else "s"
                if self._ask_yes_no(f"install {len(built)} built snap{many}?"):
                    for snap, made in built:
                        self._install(snap, made)
                else:
                    self.say(f"built {len(built)}, installed none", "dim")

        self.run_job("updating", work)

    def build_selected(self):
        """Hand the selected project to snapcraft as it stands."""
        row = self.row
        if row is None:
            return

        def work():
            reporter = DashboardReporter(self, row)
            row.state = "working"
            self.status = f"building {row.name}"
            project.adopt(row.snap, reporter)
            self._build(row.snap, reporter, row)
            self.idle()

        self.run_job("building", work)

    def _build(self, snap, reporter, row=None, ask_install=True):
        """Build, and turn a failure into a red line rather than a crash."""
        try:
            built = project.build(snap, reporter)
            self.db.add(snap)
            if row:
                row.state = "built"
            self.say(f"built {built.name}", "bold green")
        except project.ForgeError as exc:
            if row:
                row.state, row.note = "failed", str(exc)
            self.say(f"{snap.name}: {exc}", "red")
            return None

        # Offered, never assumed: this is the one thing here that needs root.
        # A run of updates asks once at the end instead, not once per snap.
        if not ask_install:
            return built
        if self._ask_yes_no(f"install {built.name}?"):
            self._install(snap, built)
        else:
            self.say(f"built {built.name}, not installed", "dim")
        return built

    def delete_selected(self):
        """Forget the selected snap. Asked about first -- it is not undoable."""
        row = self.row
        if row is None or self.busy:
            return
        name = row.name
        self.db.remove(name)
        self.reload()
        self.say(f"removed {name} from the register, and its recipe with it",
                 "yellow")
        self.say(f"the project directory is still at {row.snap.path}", "dim")
        self.idle()

    def render(self):
        """One frame of whatever the board currently is."""
        return self.screen.render()

    def select(self, name):
        for index, row in enumerate(self.rows):
            if row.name == name:
                self.cursor = index
                return

    def row_for(self, name):
        return next((r for r in self.known if r.name == name), None)

    # -- keys ----------------------------------------------------------------

    def handle(self, key):
        # Whatever is up has the keyboard: the list only gets what is left.
        mode = self.mode
        if mode:
            return HANDLERS[mode](self, key)
        if key == "q":
            # Only q: Escape means "not this", and there is nothing to leave.
            if self.busy:
                self.cancel.set()
                self.say("stopping after this step ...", "yellow")
            else:
                self.quit = True
        elif key == "escape":
            if self.busy:
                self.cancel.set()
                self.say("stopping after this step ...", "yellow")
        elif key in ("j", "down"):
            self.move(1)
        elif key in ("k", "up"):
            self.move(-1)
        elif key == "pagedown":
            self.move(self.screen.window)
        elif key == "pageup":
            self.move(-self.screen.window)
        elif key == "home":
            self.cursor = 0
        elif key in ("end", "G"):
            self.cursor = max(0, len(self.rows) - 1)
        elif key == "n":
            self.prompting, self.prompt = True, ""
        elif key == "r":
            self.recheck()
        elif key == "u":
            self.update_selected()
        elif key == "U":
            self.update_all()
        elif key == "b":
            self.build_selected()
        elif key == "g":
            self.pull_database()
        elif key == "l":
            self.reading_log, self.log_offset = True, 0
        elif key == "/":
            self.filtering = True
        elif key == "s":
            self.order = "attention" if self.order == "register" else "register"
            self.restack()
            self.say(f"ordered by {self.order}", "dim")
        elif key == "?":
            self.helping = True
        elif key == "t" and self.row and not self.busy:
            # Seeded with what it tracks now, so changing one word is one word.
            self.tracking = self.row.name
            self.prompt = sources.summarise(self.row.snap.upstream) \
                if self.row.snap.upstream else ""
        elif key == "d" and self.row and not self.busy:
            self.confirm = self.row.name
        elif key == "enter" and self.row:
            self.detail = self.row.snap

    def _showing(self, key):
        """A record, or the keys page: any key puts it away."""
        self.detail, self.helping = None, False

    def _typing(self, key):
        """The one box: find something registered, or add something new."""
        if key == "escape":
            self._close_prompt()
        elif key in ("down",):
            self.match_cursor = min(self.match_cursor + 1,
                                    max(0, len(self.matches) - 1))
        elif key in ("up",):
            self.match_cursor = max(self.match_cursor - 1, 0)
        elif key == "enter":
            text = self.prompt.strip()
            chosen = (self.matches[self.match_cursor]
                      if self.matches and self.match_cursor < len(self.matches)
                      else None)
            self._close_prompt()
            if chosen is not None:
                self.package(chosen)
            elif text:
                self.create(text)
        else:
            typed = edited(self.prompt, key)
            if typed is None:
                return
            self.prompt = typed
            self.matches = self.db.search(self.prompt)
            self.match_cursor = 0

    def _typing_track(self, key):
        """Where the selected snap's releases should be looked for."""
        if key == "escape":
            self.tracking, self.prompt = "", ""
        elif key == "enter":
            name, text = self.tracking, self.prompt.strip()
            self.tracking, self.prompt = "", ""
            self.track(name, text)
        else:
            typed = edited(self.prompt, key)
            if typed is not None:
                self.prompt = typed

    def _reading(self, key):
        """Scrolling back through what has already gone past."""
        page = max(1, self.screen.window)
        oldest = max(0, len(self.log) - 1)
        if key in ("escape", "q", "l"):
            self.reading_log = False
        elif key in ("k", "up"):
            self.log_offset = min(self.log_offset + 1, oldest)
        elif key in ("j", "down"):
            self.log_offset = max(self.log_offset - 1, 0)
        elif key == "pageup":
            self.log_offset = min(self.log_offset + page, oldest)
        elif key == "pagedown":
            self.log_offset = max(self.log_offset - page, 0)
        elif key == "home":
            self.log_offset = oldest
        elif key in ("end", "G"):
            self.log_offset = 0

    def _typing_filter(self, key):
        """Narrowing the list as it is typed, over name, repo, summary, kind."""
        if key == "escape":
            self.filtering, self.needle = False, ""
        elif key == "enter":
            self.filtering = False
        else:
            typed = edited(self.needle, key, lower=True)
            if typed is None:
                return
            self.needle = typed
        self.restack()

    def _close_prompt(self):
        self.prompting, self.prompt = False, ""
        self.matches, self.match_cursor = [], 0

    def _choosing(self, key):
        total = len(self.picking.candidates)
        if key in ("j", "down"):
            self.pick_cursor = min(self.pick_cursor + 1, total - 1)
        elif key in ("k", "up"):
            self.pick_cursor = max(self.pick_cursor - 1, 0)
        elif key.isdigit() and 1 <= int(key) <= total:
            self.pick_cursor = int(key) - 1
            self.picked.set()
        elif key == "enter":
            self.picked.set()
        elif key in ("escape", "q"):
            self.pick_cursor = -1
            self.picked.set()

    def _answering(self, key):
        """y or n for the question the worker is blocked on."""
        if key in ("y", "Y"):
            self.answer = True
        elif key in ("n", "N", "escape", "q", "enter"):
            self.answer = False
        else:
            return
        self.answered.set()

    def _confirming(self, key):
        name, self.confirm = self.confirm, ""
        if key in ("y", "Y"):
            self.select(name)
            self.delete_selected()
        else:
            self.say(f"left {name} alone", "dim")

    @contextmanager
    def suspended(self):
        """Give the terminal back, for something that wants to write to it."""
        live, keyboard = self.live, self.keyboard
        if keyboard:
            keyboard.pause()
        if live:
            live.stop()
        try:
            yield
        finally:
            if live:
                live.start()
            if keyboard:
                keyboard.resume()


# -- drawing helpers ---------------------------------------------------------


class DashboardReporter(Reporter):
    """Everything the work says, into the dashboard's log pane."""

    # The build's output belongs in the log pane, not on a vacated terminal.
    captures_output = True

    def __init__(self, dashboard, row):
        self.dashboard = dashboard
        self.row = row

    def _check_cancelled(self):
        if self.dashboard.cancel.is_set():
            raise Cancelled()

    def _lines(self, text, style, prefix="", rest="    "):
        """One log entry per line."""
        for index, line in enumerate(str(text).split("\n")):
            self.dashboard.say(f"{prefix if index == 0 else rest}{line}", style)

    def step(self, text):
        self._check_cancelled()
        self._lines(text, "cyan", prefix="==> ")

    def detail(self, text):
        self._lines(text, "", prefix="    ")

    def warn(self, text):
        self._lines(text, "yellow", prefix="    warning: ")

    def result(self, text):
        self._lines(text, "bold green", prefix="==> ")

    def output(self, line):
        self._check_cancelled()
        self._lines(line, "dim", prefix="  ", rest="  ")

    def progress(self, done, total):
        self._check_cancelled()
        if self.row:
            self.row.done_bytes, self.row.total_bytes = done, total
        elif total:
            self.dashboard.status = (f"downloading {done / 1e6:.0f}"
                                     f"/{total / 1e6:.0f} MB")

    def suspended(self):
        return self.dashboard.suspended()


# One handler for every mode, so a mode cannot be added and go unanswered.
HANDLERS = {
    "asking": Dashboard._answering,
    "tracking": Dashboard._typing_track,
    "prompting": Dashboard._typing,
    "picking": Dashboard._choosing,
    "confirm": Dashboard._confirming,
    "detail": Dashboard._showing,
    "helping": Dashboard._showing,
    "reading_log": Dashboard._reading,
    "filtering": Dashboard._typing_filter,
}


def run_dashboard(db):
    dashboard = Dashboard(db=db)
    if dashboard.rows:
        dashboard.recheck()

    with Keyboard() as keyboard:
        dashboard.keyboard = keyboard
        with Live(dashboard.render(), screen=True, refresh_per_second=REFRESH,
                  redirect_stdout=False, redirect_stderr=False) as live:
            dashboard.live = live
            while not dashboard.quit:
                for key in keyboard.keys(1 / REFRESH):
                    if key:
                        dashboard.handle(key)
                    if dashboard.quit:
                        break
                live.update(dashboard.render())

    if dashboard.worker and dashboard.worker.is_alive():
        dashboard.cancel.set()
        print("finishing what is in flight ...", flush=True)
        dashboard.worker.join(timeout=15)
    return 0
