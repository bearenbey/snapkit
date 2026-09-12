"""The dashboard: the keys, the one worker thread, and the drawing."""

import contextlib
import io
import tempfile
import threading
import time
from pathlib import Path

from snapforge import db
from snapforge.tui import Dashboard

from .harness import check, same, patched, wait_for, _catch, make_deb


@check("every header the find-or-add box can draw actually draws")
def _():
    # screen.py used local.looks_like_path() and never imported it.

    with tempfile.TemporaryDirectory() as home:
        board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
        board.prompting = True
        for typed in ("", "owner/name", "./thing.deb", str(Path(home)),
                      "~/Downloads"):
            board.prompt, board.matches = typed, []
            board.screen.render()


@check("the picker blocks the worker until something is chosen")
def _():
    from snapforge.tui import Cancelled, Dashboard

    class Candidate:
        def __init__(self, name):
            self.name, self.kind, self.why = name, "archive", "because"

    class Release:
        tag = "v1"

    class Plan:
        repo, release = "a/b", Release()
        candidates = [Candidate("one.tar.gz"), Candidate("two.deb"),
                      Candidate("three.AppImage")]

    with tempfile.TemporaryDirectory() as home:
        board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
        for keys, want in ((["down", "enter"], "two.deb"),
                           (["3"], "three.AppImage"),
                           (["enter"], "one.tar.gz")):
            out = {}
            board.run_job("creating", lambda out=out: out.update(
                got=board._ask_which(Plan()).name))
            wait_for(lambda: board.picking is not None, 50, 0.02)
            assert board.picking is not None, "the picker never appeared"
            assert not out, "it chose without being asked"
            for key in keys:
                board.handle(key)
            wait_for(lambda out=out: out, 100, 0.02)
            same(out.get("got"), want, f"keys {keys}")
            assert board.picking is None, "the picker stayed up"

        # escape gives up on the whole create
        out = {}
        board.run_job("creating", lambda: out.update(
            raised=_catch(board, Plan, Cancelled)))
        wait_for(lambda: board.picking is not None, 50, 0.02)
        board.handle("escape")
        wait_for(lambda: out, 100, 0.02)
        same(out.get("raised"), True, "escape did not cancel")


@check("the find-or-add box searches as you type and picks what it finds")
def _():
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        store.add(db.Snap(name="btop", repo="aristocratos/btop",
                          summary="A monitor of resources"))
        store.add(db.Snap(name="bat", repo="sharkdp/bat", summary="a cat clone"))
        board = Dashboard(db=store)
        board.handle("n")
        for character in "monitor":
            board.handle(character)
        same([s.name for s in board.matches], ["btop"], "typing did not search")
        board.handle("backspace")
        same(board.prompt, "monito")
        for _ in "monito":
            board.handle("backspace")
        same(board.prompt, "", "backspace did not empty it")

        # a repository nothing matches falls through to making a new one
        for character in "sharkdp/hyperfine":
            board.handle(character)
        same(board.matches, [], "an unknown repo should match nothing")
        board.handle("escape")
        same(board.prompting, False)
        same(board.matches, [], "escape left the matches behind")


@check("the header is tall enough to show what it is showing")
def _():
    # Fixed at three rows, the matches were drawn into nothing.
    from rich.console import Console
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        for index in range(4):
            store.add(db.Snap(name=f"thing{index}", repo=f"a/thing{index}"))
        board = Dashboard(db=store)
        board.handle("n")
        for character in "thing":
            board.handle(character)
        assert len(board.matches) == 4, board.matches
        assert board.screen._header_height() >= 4 + 4, board.screen._header_height()
        buffer = io.StringIO()
        Console(file=buffer, width=100, height=30).print(board.render())
        drawn = buffer.getvalue()
        for index in range(4):
            assert f"thing{index}" in drawn.split("registered")[0], \
                f"thing{index} was clipped out of the header"


@check("the dashboard actually starts its work")
def _():
    # Every action set `busy` then hit a guard refusing anything busy.
    with tempfile.TemporaryDirectory() as home:
        board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
        ran = threading.Event()
        assert board.run_job("testing", ran.set) is True, "job refused"
        assert ran.wait(5), "the job never ran"
        wait_for(lambda: not board.busy, 50, 0.05)
        same(board.busy, "", "busy was not cleared")


@check("the dashboard refuses a second job while one is running")
def _():
    with tempfile.TemporaryDirectory() as home:
        board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
        release = threading.Event()
        board.run_job("first", release.wait)
        assert board.run_job("second", lambda: None) is False, "overlap allowed"
        release.set()


@check("a job that raises says so and does not wedge the dashboard")
def _():
    with tempfile.TemporaryDirectory() as home:
        board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))

        def boom():
            raise RuntimeError("deliberate")
        board.run_job("boom", boom)
        wait_for(lambda: not board.busy, 50, 0.05)
        same(board.busy, "", "busy stuck after a failure")
        assert "deliberate" in str(list(board.log)[-1]), list(board.log)


@check("an arrow key is an arrow key, not an Escape")
def _():
    # stdin's buffer hid the rest of an arrow key, so it read as Escape.
    from snapforge.keys import Keyboard
    reader = Keyboard.__new__(Keyboard)
    reader.pending = ""
    for data, want in (("\x1b[A", ["up"]), ("\x1b[B", ["down"]),
                       ("\x1bOA", ["up"]), ("\x1bOB", ["down"]),
                       ("\x1b[C", ["right"]), ("\x1b[D", ["left"]),
                       ("\x1b[5~", ["pageup"]), ("\x1b[6~", ["pagedown"]),
                       ("\x1b[H", ["home"]), ("\x1b[F", ["end"]),
                       ("\r", ["enter"]), ("\x7f", ["backspace"]),
                       ("\x1b[A\x1b[A\x1b[B", ["up", "up", "down"]),
                       ("abc", ["a", "b", "c"])):
        reader.pending = ""
        same(reader._parse(data), want, repr(data))

    # a sequence split across two reads is still one key
    reader.pending = ""
    same(reader._parse("\x1b"), [], "half an arrow produced a key")
    same(reader._parse("[A"), ["up"], "the other half was lost")

    # and a real Escape is held, to be told apart from an arrow
    reader.pending = ""
    reader._parse("\x1b")
    same(reader.pending, "\x1b", "Escape was not held back")

    # a sequence nothing here knows (F1 on the console) must not keep
    # every key after it in the buffer for ever
    reader.pending = ""
    reader._parse("\x1b[[A")
    same(reader._parse("q"), ["q"], "a key after an unknown sequence was lost")
    same(reader.pending, "", "an unknown sequence was held for ever")


@check("only q quits")
def _():
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        store.add(db.Snap(name="demo", repo="a/b"))
        board = Dashboard(db=store)
        for key in ("up", "down", "left", "right", "escape", "home", "end",
                    "pageup", "pagedown", "x", "tab"):
            board.handle(key)
            same(board.quit, False, f"{key} quit the dashboard")
        board.handle("q")
        same(board.quit, True, "q did not quit")


@check("every key the legend advertises reaches what it advertises")
def _():
    from snapforge import screen
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        for index in range(5):
            store.add(db.Snap(name=f"s{index}", repo=f"a/b{index}"))
        board = Dashboard(db=store)

        called = []
        for name in ("recheck", "update_selected", "build_selected",
                     "pull_database", "update_all"):
            setattr(board, name, lambda name=name: called.append(name))

        # g read the database, and an earlier binding took it for `home`.
        board.cursor = 3
        board.handle("g")
        same(called, ["pull_database"], "g did not read the database")
        same(board.cursor, 3, "g moved the cursor instead")

        for key, name in (("r", "recheck"), ("u", "update_selected"),
                          ("b", "build_selected"), ("U", "update_all")):
            called.clear()
            board.handle(key)
            same(called, [name], f"{key} did not reach {name}")

        # and the ones that only change what is on screen
        board.handle("l")
        same(board.reading_log, True, "l did not open the log")
        board.handle("q")
        same((board.reading_log, board.quit), (False, False),
             "q in the log should close it, not quit the dashboard")
        board.handle("?")
        same(board.helping, True, "? did not open the keys")
        board.handle("x")
        same(board.helping, False, "the keys page would not close")
        board.handle("/")
        same(board.filtering, True, "/ did not open the filter")
        board.handle("escape")
        same((board.filtering, board.needle), (False, ""),
             "escape left the filter behind")
        was = board.order
        board.handle("s")
        assert board.order != was, "s did not change the order"

        lettered = {key for key, _ in screen.KEYS + screen.SHORT_KEYS
                    if len(key) == 1}
        same(lettered - set("nrub/l?q"), set(),
             "a key is advertised in the footer that nothing here presses")


@check("the list scrolls, and the cursor stays inside it")
def _():
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        for index in range(20):
            store.add(db.Snap(name=f"s{index:02d}", repo=f"a/b{index}"))
        board = Dashboard(db=store)
        board.screen.window = 8

        same(board.screen._window(), (0, 8), "the first window is wrong")
        for _ in range(10):
            board.handle("down")
        first, last = board.screen._window()
        assert first <= board.cursor < last, \
            f"cursor {board.cursor} outside the drawn window {first}-{last}"

        board.handle("end")
        same(board.cursor, 19, "end did not go to the last")
        first, last = board.screen._window()
        assert first <= 19 < last, f"the last row is not drawn: {first}-{last}"

        board.handle("home")
        board.screen._window()
        same((board.cursor, board.screen.offset), (0, 0), "home did not scroll back")

        # walking off either end changes nothing
        for _ in range(50):
            board.handle("up")
        same(board.cursor, 0)
        for _ in range(50):
            board.handle("down")
        same(board.cursor, 19)

        # a list shorter than the window is never scrolled
        board.rows = board.rows[:3]
        board.cursor = 0
        same(board.screen._window(), (0, 3))
        same(board.screen.offset, 0)


@check("the dashboard checks the same things the command line does")
def _():
    # Same finding, but through the recheck that held the bad pre-check.
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        project_dir = here / "demo-snap"
        project_dir.mkdir()
        make_deb(project_dir / "demo_1.0_amd64.deb", version="1.0")
        store = db.Database(here / "register")
        store.add(db.Snap(
            name="demo", style="artifact", version="1.0", kind="deb",
            asset="demo_1.0_amd64.deb", asset_glob="demo_*_amd64.deb",
            directory=str(project_dir),
            upstream={"kind": "local", "glob": "demo_*_amd64.deb"}))

        board = Dashboard(db=store)
        make_deb(project_dir / "demo_2.0_amd64.deb", version="2.0")
        board.recheck()
        board.worker.join(timeout=20)
        same(board.rows[0].state, "behind", "the dashboard did not check it")
        same(board.rows[0].latest, "2.0")


@check("t opens the track box seeded with what the snap tracks now")
def _():
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home))
        store.add(db.Snap(name="demo", version="1.0",
                          upstream={"kind": "local", "glob": "demo_*.deb"}))
        board = Dashboard(db=store)
        board.handle("t")
        same(board.tracking, "demo")
        same(board.prompt, "local glob=demo_*.deb",
             "editing one word should not mean typing all of them")
        # Typed into, then abandoned: nothing of it survives.
        for letter in " x":
            board.handle(letter)
        board.handle("escape")
        same((board.tracking, board.prompt), ("", ""))
        same(store.get("demo").upstream,
             {"kind": "local", "glob": "demo_*.deb"})


@check("an emptied track box is never mind, not stop tracking")
def _():
    # Backing out of `t` with a cleared line threw the upstream away.
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home))
        was = {"kind": "local", "glob": "demo_*.deb"}
        store.add(db.Snap(name="demo", version="1.0", upstream=dict(was)))
        board = Dashboard(db=store)

        board.track("demo", "")
        if board.worker:
            board.worker.join(timeout=10)
        same(store.get("demo").upstream, was, "an empty line untracked it")
        same(board.busy, "", "it should not have started any work")

        # Said outright, it still does what it says.
        board.track("demo", "none")
        board.worker.join(timeout=10)
        same(store.get("demo").upstream, {})


@check("stopping tracking clears the repository as well as the upstream")
def _():
    # Left behind, `repo` has check() fall back to it and carry on.
    from snapforge import update
    snap = db.Snap(name="demo", repo="a/b", url="https://github.com/a/b",
                   version="1.0", asset_pattern="^x$",
                   upstream={"kind": "local"})
    update.untrack(snap)
    same((snap.upstream, snap.repo, snap.url, snap.asset_pattern),
         ({}, "", "", ""))
    same(update.situation(snap).state, "untracked")


@check("the dashboard tracks, and refuses, the way the command line does")
def _():
    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        project_dir = here / "demo-snap"
        project_dir.mkdir()
        make_deb(project_dir / "demo_2.0_amd64.deb", version="2.0")
        store = db.Database(here / "register")
        store.add(db.Snap(name="demo", style="artifact", version="1.0",
                          kind="deb", asset_glob="demo_*_amd64.deb",
                          directory=str(project_dir)))
        board = Dashboard(db=store)

        board.track("demo", "folder glob=demo_*_amd64.deb")
        board.worker.join(timeout=20)
        same(store.get("demo").upstream,
             {"kind": "local", "glob": "demo_*_amd64.deb"})
        same(board.rows[0].state, "behind", "it did not check what it set")
        same(board.rows[0].latest, "2.0")

        # Written down untried, a setting reads as up to date for ever.
        board.track("demo", "local glob=nothing-like-this-*.deb")
        board.worker.join(timeout=20)
        same(store.get("demo").upstream,
             {"kind": "local", "glob": "demo_*_amd64.deb"},
             "an upstream that resolved to nothing was written down")
        assert any("left as it was" in line.plain for line in board.log), \
            "it did not say why"

        board.track("demo", "none")
        board.worker.join(timeout=20)
        same(store.get("demo").upstream, {})
        same(board.rows[0].state, "untracked")


@check("every kind reachable from the terminal is offered on the dashboard")
def _():
    # The command line grew `track` first; the dashboard is the same list.
    from snapforge import screen, sources
    offered = {form.split()[0] for form, _ in screen._TRACK_HINTS}
    same(offered, set(sources.RESOLVERS) | {"repo", "none"},
         "the dashboard and the shapes have drifted")
    assert "t" in screen.advertised(), "no key says it is there"


@check("every mode is answered by a key handler and drawn by something")
def _():
    # Two chains that had to agree left a new mode stuck on screen.
    from snapforge import screen, tui

    same(set(tui.HANDLERS), set(tui.MODES),
         "a mode has no key handler, or a handler has no mode")
    same(set(screen.FULL_SCREEN) - set(tui.MODES), set(),
         "something is drawn full screen for a mode that cannot be up")

    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        store.add(db.Snap(name="demo", repo="a/b"))
        board = tui.Dashboard(db=store)
        same(board.mode, "", "the list is not a mode")
        for name in tui.MODES:
            setattr(board, name, "x" if name != "picking" else object())
            same(board.mode, name, f"{name} did not take the screen")
            setattr(board, name, "" if name != "picking" else None)
        same(board.mode, "", "a mode was left up")


@check("a check that runs long is a timeout, not a wait")
def _():
    from snapforge import net, update

    # Nothing left on the clock: the request is refused before a socket.
    with net.deadline(0.05):
        time.sleep(0.06)
        try:
            net.get_text("https://example.invalid/never-asked")
            assert False, "should have given up"
        except net.NetworkError as exc:
            assert "timed out" in str(exc), exc

    # With time left, a request is given what is left and no more.
    asked = []

    class Opener:
        @staticmethod
        def open(request, timeout=None):
            asked.append(timeout)
            raise OSError("not answering")

    with net.deadline(2):
        try:
            net._open(Opener, "https://example.invalid/", retries=0)
        except net.NetworkError:
            pass
    assert asked and asked[0] <= 2, f"asked for {asked}, not what was left"
    same(net._left(30, "u"), 30, "the deadline outlived its block")

    # And a snap whose upstream will not answer reads as unreachable.
    def slow(snap, force=False):
        time.sleep(0.2)
        raise net.NetworkError("https://nowhere.invalid/: timed out")

    with patched(update, check=slow):
        found = update.situation(db.Snap(name="demo", repo="a/b"),
                                 timeout=0.05)
        same(found.state, "error", "a timeout is not an up-to-date")
        assert "timed out" in found.problem, found.problem


@check("a filter narrows the eye, not the register")
def _():
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        for name, repo in (("btop", "aristocratos/btop"),
                           ("zen", "zen-browser/desktop"),
                           ("floorp", "Floorp-Projects/Floorp")):
            store.add(db.Snap(name=name, repo=repo, version="1.0",
                              summary="a browser" if name != "btop"
                              else "a resource monitor"))
        board = Dashboard(db=store)
        same(len(board.rows), 3)

        board.handle("/")
        for key in "brow":
            board.handle(key)
        same([r.name for r in board.rows], ["floorp", "zen"],
             "the filter reads the summary as well as the name")
        same(len(board.known), 3, "the filter threw records away")

        # r and U work on the register, so a filter cannot hide work.
        board.known[0].state = "behind"
        same([r.name for r in board.known if r.behind], ["btop"],
             "a filtered-out row stopped being behind")

        board.handle("escape")
        same((board.needle, len(board.rows)), ("", 3), "escape left it narrowed")


@check("ordering by attention puts what needs doing at the top")
def _():
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        for name in ("aaa", "bbb", "ccc"):
            store.add(db.Snap(name=name, repo=f"o/{name}", version="1.0"))
        board = Dashboard(db=store)
        same([r.name for r in board.rows], ["aaa", "bbb", "ccc"])

        board.row_for("ccc").state = "behind"
        board.row_for("bbb").state = "failed"
        board.handle("s")
        same([r.name for r in board.rows], ["ccc", "bbb", "aaa"],
             "behind, then failed, then the rest")
        board.handle("s")
        same([r.name for r in board.rows], ["aaa", "bbb", "ccc"],
             "s did not put the register order back")


@check("the log scrolls back, and stops at both ends")
def _():
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        store.add(db.Snap(name="demo", repo="a/b"))
        board = Dashboard(db=store)
        for index in range(50):
            board.say(f"line {index}")

        # The drawing is what tells the scroll how long the page is.
        from rich.console import Console
        paper = Console(file=io.StringIO(), width=90, height=20)

        def press(key):
            board.handle(key)
            paper.print(board.render())

        press("l")
        same((board.reading_log, board.page_offset), (True, 0))
        for _ in range(5):
            press("up")
        same(board.page_offset, 5, "up did not scroll back")
        for _ in range(500):
            press("up")
        assert board.page_offset < len(board.log), "scrolled past the oldest"
        press("G")
        same(board.page_offset, 0, "G did not come back to the newest")
        press("escape")
        same(board.reading_log, False, "escape did not close the log")


@check("delete asks before it forgets")
def _():
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        store.add(db.Snap(name="demo", repo="a/b"))
        board = Dashboard(db=store)
        board.handle("d")
        same(board.confirm, "demo", "no confirmation was asked for")
        board.handle("n")
        same(store.names(), ["demo"], "answering no still deleted it")
        board.handle("d")
        board.handle("y")
        same(store.names(), [], "answering yes did not delete it")


@check("the dashboard draws every state it can be in")
def _():
    from rich.console import Console
    from snapforge.screen import STATES as STATE_STYLE
    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        for index in range(len(STATE_STYLE)):
            store.add(db.Snap(name=f"s{index}", repo=f"a/b{index}",
                              version="1.0", kind="deb"))
        board = Dashboard(db=store)
        for row, state in zip(board.rows, STATE_STYLE):
            row.state = state
        board.rows[0].done_bytes, board.rows[0].total_bytes = 1, 2
        console = Console(file=io.StringIO(), width=120, height=40)
        console.print(board.render())
        board.prompting = True
        console.print(board.render())
        board.prompting, board.confirm = False, "s0"
        console.print(board.render())
        board.confirm, board.detail = "", board.rows[0].snap
        console.print(board.render())
        # The three that take the whole screen to themselves.
        board.detail, board.helping = None, True
        console.print(board.render())
        board.helping, board.reading_log = False, True
        console.print(board.render())
        board.reading_log, board.filtering, board.needle = False, True, "s1"
        console.print(board.render())


@check("the picker is taken down by the thread that draws it")
def _():
    # The worker cleared it between the mode check and the draw, so a frame
    # read .candidates off None and the dashboard died.
    from rich.console import Console
    with tempfile.TemporaryDirectory() as home:
        board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
        board.picking = type("Plan", (), {
            "title": "x", "candidates": [type("C", (), {
                "name": "a", "kind": "deb", "why": "w"})()]})()
        board.pick_cursor = 0
        board.handle("enter")
        same(board.picking, None, "the picker stayed up after a choice")
        assert board.picked.is_set(), "the worker was not told"
        # and a frame that finds it gone draws the list, not a traceback
        console = Console(file=io.StringIO(), width=100, height=30)
        console.print(board.render())


@check("a yes/no says what it is about, not always 'install'")
def _():
    from rich.console import Console
    from snapforge.tui import Dashboard, DANGEROUS
    with tempfile.TemporaryDirectory() as home:
        board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
        for title, note in (("update", ""), ("install", DANGEROUS)):
            board.asking, board.asking_title, board.asking_note = "go?", title, note
            buffer = io.StringIO()
            Console(file=buffer, width=100, height=30).print(board.render())
            drawn = buffer.getvalue()
            assert title in drawn, f"{title} missing"
            same("--dangerous" in drawn, bool(note), f"the note for {title}")


@check("a paused keyboard still paces the loop instead of spinning it")
def _():
    from snapforge.keys import Keyboard
    reader = Keyboard.__new__(Keyboard)
    reader.saved, reader.pending = None, ""
    started = time.monotonic()
    same(reader.keys(0.05), [])
    assert time.monotonic() - started >= 0.04, "it returned at once"


@check("a worker giving the terminal back after quit does not take it again")
def _():
    # Ctrl-C at the sudo prompt: the main thread had left the screen, and
    # the worker's cleanup re-entered it with nothing left to undo that.
    with tempfile.TemporaryDirectory() as home:
        board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
        calls = []
        board.live = type("Live", (), {"stop": lambda s: calls.append("stop"),
                                       "start": lambda s: calls.append("start")})()
        with board.suspended():
            board.closing = True
        same(calls, ["stop"], "the screen was restarted after teardown")
        board.closing, calls[:] = False, []
        with board.suspended():
            pass
        same(calls, ["stop", "start"])


@check("the screen fits the terminal it is given")
def _():
    # A fixed column set squeezed REPOSITORY to nothing at eighty columns.
    from rich.console import Console
    from snapforge.screen import KEYS, _keys

    class FakeLive:          # render() reads the width off the console
        def __init__(self, console): self.console = console

    with tempfile.TemporaryDirectory() as home:
        store = db.Database(Path(home) / "snapkit.json")
        for index in range(4):
            store.add(db.Snap(name=f"thing{index}", repo=f"owner/thing{index}",
                              version="1.0.0", kind="deb"))
        board = Dashboard(db=store)
        for width in (72, 80, 96, 110, 132, 200):
            console = Console(file=io.StringIO(), width=width, height=30)
            board.live = FakeLive(console)
            console.print(board.render())
            for line in console.file.getvalue().splitlines():
                assert len(line.rstrip()) <= width, \
                    f"a line ran past {width} columns: {line!r}"
            console.file.truncate(0), console.file.seek(0)

            # every column that was drawn got room to say something
            console.print(board.screen._table(width))
            drawn = console.file.getvalue()
            assert "thing0" in drawn, f"the list vanished at {width} columns"

        # the inspector appears when there is room and not before
        wide = Console(file=io.StringIO(), width=132, height=30)
        board.live = FakeLive(wide)
        wide.print(board.render())
        assert "inspector" in wide.file.getvalue(), "no inspector at 132 columns"
        narrow = Console(file=io.StringIO(), width=90, height=30)
        board.live = FakeLive(narrow)
        narrow.print(board.render())
        assert "inspector" not in narrow.file.getvalue(), \
            "the inspector was drawn where there is no room for it"

        # and the legend shortens rather than being cut off
        same(len(board.screen._footer(200).plain) <= 200, True)
        assert len(board.screen._footer(60).plain) <= 60, "the legend overran"
        assert len(_keys(KEYS).plain) > 60, "this test proves nothing"


@check("the moving parts move")
def _():
    from snapforge.screen import _ago, _smooth_bar, _spinner
    # a bar that only moves a whole cell at a time looks stuck
    seen = {_smooth_bar(n / 100, 12) for n in range(101)}
    assert len(seen) > 12, f"only {len(seen)} distinct bars over 100 steps"
    same(_smooth_bar(0, 12), "░" * 12)
    same(_smooth_bar(1, 12), "█" * 12)
    for share in (-1, 0.5, 2):
        same(len(_smooth_bar(share, 12)), 12, f"wrong width at {share}")
    same(len({_spinner(f) for f in range(24)}) > 1, True, "the spinner is still")
    same(_ago(""), "")
    same(_ago("not a date"), "")
    assert _ago(db.now()) in ("now", "0s"), _ago(db.now())


@check("a build's output goes into the log pane, not over the screen")
def _():
    from snapforge.tui import Dashboard, DashboardReporter

    with tempfile.TemporaryDirectory() as home:
        board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
        reporter = DashboardReporter(board, None)
        # Unset, project.build hands the terminal over for the whole build.
        same(reporter.captures_output, True)

        reporter.output("Priming btop")
        assert any("Priming btop" in str(line) for line in board.log), \
            list(board.log)

        # The pane is a list of lines, so a 3-line note is 3 entries.
        before = len(board.log)
        reporter.detail("install it with:\n      snap install x\n      snap connect y")
        added = list(board.log)[before:]
        same(len(added), 3)
        assert not any("\n" in str(line) for line in added), added


@check("the install question blocks the worker until a key answers it")
def _():

    with tempfile.TemporaryDirectory() as home:
        board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))

        for keys, want in ((["y"], True), (["Y"], True), (["n"], False),
                           (["escape"], False), (["enter"], False)):
            out = {}
            board.run_job("building", lambda out=out: out.update(
                got=board._ask_yes_no("install x.snap?")))
            wait_for(lambda: board.asking, 50, 0.02)
            same(board.asking, "install x.snap?")
            for key in keys:
                board.handle(key)
            wait_for(lambda out=out: "got" in out, 50, 0.02)
            same(out.get("got"), want, f"{keys} answered wrongly")
            same(board.asking, "")


@check("nothing else answers the install question by accident")
def _():

    with tempfile.TemporaryDirectory() as home:
        board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
        out = {}
        board.run_job("building", lambda: out.update(
            got=board._ask_yes_no("install x.snap?")))
        wait_for(lambda: board.asking, 50, 0.02)

        # An arrow key used to read as a move, and a move is not an answer.
        for key in ("j", "k", "down", "up", "r", "b", "3"):
            board.handle(key)
        time.sleep(0.15)
        same("got" in out, False, "an unrelated key answered it")
        board.handle("n")
        wait_for(lambda: "got" in out, 50, 0.02)
        same(out.get("got"), False)


@check("cancelling while the question is up counts as no, and does not hang")
def _():

    with tempfile.TemporaryDirectory() as home:
        board = Dashboard(db=db.Database(Path(home) / "snapkit.json"))
        out = {}
        board.run_job("building", lambda: out.update(
            got=board._ask_yes_no("install x.snap?")))
        wait_for(lambda: board.asking, 50, 0.02)
        board.cancel.set()
        wait_for(lambda: "got" in out, 100, 0.02)
        same(out.get("got"), False, "cancelling did not answer it")


@check("installing asks for root once, and only classic snaps get --classic")
def _():
    import types
    from snapforge import tui

    with tempfile.TemporaryDirectory() as home:
        here = Path(home)
        strict, classic = here / "a", here / "b"
        for path, confinement in ((strict, "strict"), (classic, "classic")):
            (path / "snap").mkdir(parents=True)
            (path / "snap" / "snapcraft.yaml").write_text(
                f"name: x\nconfinement: {confinement}\n")

        board = Dashboard(db=db.Database(here / "snapkit.json"))
        board.suspended = lambda: contextlib.nullcontext()

        ran = []
        with patched(tui, subprocess=types.SimpleNamespace(
                run=lambda argv, **kw: ran.append(argv)
                or types.SimpleNamespace(returncode=0))):
            board._install(types.SimpleNamespace(name="x", path=str(strict)),
                           here / "x_1_amd64.snap")
            same(ran[-1], ["sudo", "snap", "install", "--dangerous",
                           str(here / "x_1_amd64.snap")])

            board._install(types.SimpleNamespace(name="x", path=str(classic)),
                           here / "x_1_amd64.snap")
            assert "--classic" in ran[-1], ran[-1]
