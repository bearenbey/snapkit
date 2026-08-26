"""The dashboard: its state, its keys, and the one worker thread."""

import subprocess
import threading
from collections import deque
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass, field

from rich.live import Live
from rich.text import Text

from . import adopt, github, local, project, snapdb, update
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


@dataclass
class Dashboard:
    """Everything on screen, and the one thread allowed to change it."""

    db: object
    rows: list = field(default_factory=list)
    cursor: int = 0
    busy: str = ""
    status: str = ""
    prompt: str = ""          # what is being typed, "" when not typing
    prompting: bool = False
    confirm: str = ""         # a snap awaiting a yes before it is forgotten
    picking: object = None    # a Plan waiting for its asset to be chosen
    pick_cursor: int = 0
    asking: str = ""          # a yes/no the worker is blocked on
    matches: list = field(default_factory=list)   # what the prompt found
    match_cursor: int = 0
    detail: object = None     # a Snap being looked at, or None
    quit: bool = False
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
        self.status = (f"{len(self.rows)} registered -- press n to make one from "
                       f"a github repository" if self.rows else
                       "nothing registered yet -- press n and paste a github url")

    # -- state ---------------------------------------------------------------

    def reload(self):
        with self.lock:
            keep = {r.name: r for r in self.rows}
            self.rows = [keep.get(s.name) or Row(snap=s) for s in self.db.all()]
            for row in self.rows:
                row.snap = self.db.snaps.get(row.name, row.snap)
            self.cursor = min(self.cursor, max(0, len(self.rows) - 1))

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

    def recheck(self):
        """Ask every registered repository what it has now."""
        if not self.rows:
            return
        rows = list(self.rows)

        def work():
            for row in rows:
                row.state, row.note = "checking", ""
            self.status = f"checking {len(rows)} repositor" + \
                          ("y" if len(rows) == 1 else "ies")
            for row in rows:
                if self.cancel.is_set():
                    row.state = "unknown"
                    continue
                # Skipping on `repo` missed five snaps.
                found = update.situation(row.snap)
                row.state = found.state
                row.release, row.asset = found.release, found.asset
                row.latest = found.latest
                row.note = found.note or found.problem
                if found.note:
                    self.say(f"{row.name}: {found.note}", "yellow")
                if found.state == "error":
                    self.say(f"{row.name}: {found.problem}", "red")
            behind = sum(1 for r in self.rows if r.behind)
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
                    self.status = f"{len(self.rows)} registered"
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
                self.status = f"{len(self.rows)} registered"
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
            self.status = f"{len(self.rows)} registered"

        self.run_job("pulling", work)

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

    def _install(self, snap, built, reporter):
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
            self.status = f"{len(self.rows)} registered"

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
            self.status = f"{len(self.rows)} registered"

        self.run_job("building", work)

    def _build(self, snap, reporter, row=None):
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
            return

        # Offered, never assumed: this is the one thing here that needs root.
        if self._ask_yes_no(f"install {built.name}?"):
            self._install(snap, built, reporter)
        else:
            self.say(f"built {built.name}, not installed", "dim")

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
        self.status = f"{len(self.rows)} registered"

    def render(self):
        """One frame of whatever the board currently is."""
        return self.screen.render()

    def select(self, name):
        for index, row in enumerate(self.rows):
            if row.name == name:
                self.cursor = index
                return

    def row_for(self, name):
        return next((r for r in self.rows if r.name == name), None)

    # -- keys ----------------------------------------------------------------

    def handle(self, key):
        # First: the worker is blocked, and any other key would mean something.
        if self.asking:
            return self._answering(key)
        if self.prompting:
            return self._typing(key)
        if self.picking is not None:
            return self._choosing(key)
        if self.confirm:
            return self._confirming(key)
        if self.detail is not None:
            self.detail = None
            return
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
        elif key in ("home", "g"):
            self.cursor = 0
        elif key in ("end", "G"):
            self.cursor = max(0, len(self.rows) - 1)
        elif key == "n":
            self.prompting, self.prompt = True, ""
        elif key == "r":
            self.recheck()
        elif key == "u":
            self.update_selected()
        elif key == "b":
            self.build_selected()
        elif key == "g":
            self.pull_database()
        elif key == "d" and self.row and not self.busy:
            self.confirm = self.row.name
        elif key == "enter" and self.row:
            self.detail = self.row.snap

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
            if key == "backspace":
                self.prompt = self.prompt[:-1]
            elif key and len(key) == 1 and key.isprintable():
                self.prompt += key
            else:
                return
            self.matches = self.db.search(self.prompt)
            self.match_cursor = 0

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

    # -- drawing -------------------------------------------------------------


    # -- the header ----------------------------------------------------------


    # -- the list ------------------------------------------------------------


    # -- the inspector -------------------------------------------------------


    # -- the rest of the screen ----------------------------------------------


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

    live = None


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
