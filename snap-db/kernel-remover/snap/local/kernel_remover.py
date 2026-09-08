#!/usr/bin/env python3
"""
kernel_remover.py - safely remove old Ubuntu kernels and their leftovers.

Interactive TUI by default; plain text mode with --plain or when not on a
terminal. Standard library only, Python 3.8 or newer; nothing to install.

Safety rules, never overridden:
  * the running kernel is never removed;
  * the newest installed kernel is never removed;
  * the N newest kernels are kept (--keep, default 2; +/- in the TUI);
  * metapackages (linux-image-generic, linux-generic-hwe-*, ...) are never
    touched, and the run is refused if apt would remove one as collateral;
  * packages on hold (apt-mark hold, dpkg "hi") are never removed;
  * nothing is removed without confirmation.

What it can clean:
  1. old kernel packages: image, headers, modules, tools, nvidia/dkms, ...;
  2. leftover configuration of already-removed kernel packages ("rc" state),
     offered even when there is no old kernel to remove;
  3. leftover configuration of any other removed package (off by default);
  4. kernel files in /boot that no package owns and no installed kernel
     version accounts for (off by default).

Usage:
  sudo ./kernel_remover.py                # TUI
  ./kernel_remover.py --dry-run           # TUI without root; apply only prints
  sudo ./kernel_remover.py --plain --yes  # non-interactive, defaults only
"""

from __future__ import annotations

import argparse
import curses
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Iterable, NoReturn

BOOT = "/boot"
DEFAULT_KEEP = 2

# Versioned kernel packages: linux-image-6.8.0-45-generic,
# linux-headers-6.8.0-45, linux-hwe-6.8-headers-6.8.0-45,
# linux-modules-nvidia-535-6.8.0-45-generic. Metapackages such as
# linux-generic-hwe-24.04 carry no <major>.<minor>.<patch>-<abi> and never match.
PKG_RE = re.compile(
    r"^linux-(?P<kind>[a-z0-9.-]+?)-(?P<ver>\d+\.\d+\.\d+-\d+)(?:-(?P<flavor>[a-z0-9-]+))?$"
)
# What a kernel leaves in /boot: vmlinuz-6.8.0-45-generic, initrd.img-..., ...
BOOT_FILE_RE = re.compile(
    r"^(vmlinuz|initrd\.img|System\.map|config|retpoline)-(?P<ver>\d+\.\d+\.\d+-\d+)(-[a-z0-9-]+)?$"
)
# uname -r: 6.8.0-45-generic
RELEASE_RE = re.compile(r"^(?P<ver>\d+\.\d+\.\d+-\d+)(?:-(?P<flavor>[a-z0-9-]+))?$")


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Pkg:
    name: str
    status: str            # ii installed / hi installed, on hold / rc removed, config left
    size: int              # bytes, from Installed-Size
    version: str | None    # 6.8.0-45 for versioned kernel packages, else None
    flavor: str | None = None

    @property
    def installed(self) -> bool:
        return self.status in ("ii", "hi")

    @property
    def removed(self) -> bool:
        return self.status == "rc"

    @property
    def is_kernel(self) -> bool:
        return self.name.startswith("linux-")


@dataclass
class Host:
    """Everything the planner reads from the system. Tests build one by hand."""
    running: str                       # 6.8.0-45
    packages: list[Pkg]
    held: set[str]                     # apt-mark showhold
    boot_files: list[str]              # names in /boot
    owned: Callable[[str], bool]       # does dpkg own this path?
    is_root: bool


@dataclass
class Plan:
    running: str
    keep: int
    versions: list[str]                 # every installed kernel version, newest first
    protected: list[str]                # versions that stay, newest first
    old: dict[str, list[Pkg]]           # version -> removable packages, oldest version first
    held: list[Pkg]                     # old, but on hold
    kernel_rc: list[Pkg]                # rc state, linux-*
    other_rc: list[Pkg]                 # rc state, everything else
    orphans: list[tuple[str, int]]      # (path, bytes) in /boot
    is_root: bool
    sizes: dict[str, int] = field(default_factory=dict)   # selection key -> bytes

    def size_of(self, keys: Iterable[str]) -> int:
        return sum(self.sizes.get(k, 0) for k in keys)


@dataclass
class Options:
    keep: int = DEFAULT_KEEP
    dry_run: bool = False
    plain: bool = False
    yes: bool = False
    all_rc: bool = False
    purge_orphans: bool = False


@dataclass
class Selection:
    """What to act on. Keys are pkg:NAME, rc:NAME and file:PATH."""
    pkgs: list[str] = field(default_factory=list)   # installed packages to purge
    rc: list[str] = field(default_factory=list)     # rc packages to purge
    files: list[str] = field(default_factory=list)  # /boot orphans to delete

    @classmethod
    def from_keys(cls, keys: Iterable[str]) -> Selection:
        sel = cls()
        buckets = {"pkg": sel.pkgs, "rc": sel.rc, "file": sel.files}
        for key in sorted(keys):
            kind, _, value = key.partition(":")
            buckets[kind].append(value)
        return sel

    def empty(self) -> bool:
        return not (self.pkgs or self.rc or self.files)


@dataclass
class Refusal:
    title: str
    lines: list[str]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def version_key(ver: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.split(r"[.-]", ver))


def human(n: float) -> str:
    for unit in "BKMGT":
        if abs(n) < 1024:
            return f"{n:.0f}B" if unit == "B" else f"{n:.1f}".rstrip("0").rstrip(".") + unit
        n /= 1024
    return f"{n:.1f}P"


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def boot_free() -> int:
    st = os.statvfs(BOOT)
    return st.f_bavail * st.f_frsize


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def read_host() -> Host:
    release = os.uname().release
    m = RELEASE_RE.match(release)
    if not m:
        die(f"cannot parse the running kernel release {release!r}")
    try:
        boot_files = sorted(os.listdir(BOOT))
    except OSError:
        boot_files = []
    return Host(
        running=m.group("ver"),
        packages=list_packages(),
        held=set(run(["apt-mark", "showhold"], check=False).stdout.split()),
        boot_files=boot_files,
        owned=lambda path: run(["dpkg", "-S", path], check=False).returncode == 0,
        is_root=os.geteuid() == 0,
    )


def list_packages() -> list[Pkg]:
    res = run(
        ["dpkg-query", "-W", "-f=${Package}\t${db:Status-Abbrev}\t${Installed-Size}\n"],
        check=False,
    )
    pkgs = []
    for line in res.stdout.splitlines():
        try:
            name, status, size = line.split("\t")
        except ValueError:
            continue
        m = PKG_RE.match(name)
        pkgs.append(Pkg(
            name=name,
            status=status.strip()[:2],
            size=int(size) * 1024 if size.isdigit() else 0,
            version=m.group("ver") if m else None,
            flavor=m.group("flavor") if m else None,
        ))
    return pkgs


def orphaned_boot_files(host: Host, installed_versions: set[str]) -> list[tuple[str, int]]:
    """Kernel files in /boot of a version that is not installed and that dpkg
    does not own. Files of installed versions are skipped even when dpkg does
    not own them: initrd images are generated rather than shipped, and the
    package's own removal takes them away."""
    orphans = []
    for name in sorted(host.boot_files):
        m = BOOT_FILE_RE.match(name)
        if not m or m.group("ver") in installed_versions:
            continue
        path = os.path.join(BOOT, name)
        if host.owned(path):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        orphans.append((path, size))
    return orphans


def build_plan(host: Host, keep: int) -> Plan:
    installed = [p for p in host.packages if p.installed and p.version]
    versions = sorted({p.version for p in installed}, key=version_key, reverse=True)
    protected = set(versions[:keep]) | {host.running}

    old: dict[str, list[Pkg]] = {}
    held: list[Pkg] = []
    for p in sorted(installed, key=lambda p: p.name):
        if p.version in protected:
            continue
        if p.status == "hi" or p.name in host.held:
            held.append(p)
        else:
            old.setdefault(p.version, []).append(p)

    rc = sorted((p for p in host.packages if p.removed), key=lambda p: p.name)
    orphans = orphaned_boot_files(host, set(versions) | {host.running})

    sizes = {f"pkg:{p.name}": p.size for pkgs in old.values() for p in pkgs}
    sizes.update((f"file:{path}", size) for path, size in orphans)

    return Plan(
        running=host.running,
        keep=keep,
        versions=versions,
        protected=sorted(protected, key=version_key, reverse=True),
        old=dict(sorted(old.items(), key=lambda kv: version_key(kv[0]))),
        held=held,
        kernel_rc=[p for p in rc if p.is_kernel],
        other_rc=[p for p in rc if not p.is_kernel],
        orphans=orphans,
        is_root=host.is_root,
        sizes=sizes,
    )


def default_selection(plan: Plan, opts: Options) -> set[str]:
    """What is ticked before the user touches anything."""
    keys = {f"pkg:{p.name}" for pkgs in plan.old.values() for p in pkgs}
    keys |= {f"rc:{p.name}" for p in plan.kernel_rc}
    if opts.all_rc:
        keys |= {f"rc:{p.name}" for p in plan.other_rc}
    if opts.purge_orphans:
        keys |= {f"file:{path}" for path, _ in plan.orphans}
    return keys


# --------------------------------------------------------------------------- #
# checks and execution, shared by the TUI and plain mode
# --------------------------------------------------------------------------- #
def collateral(asked: Iterable[str], simulation: str) -> list[str]:
    """Packages an `apt-get -s` transcript removes beyond the ones asked for."""
    asked = set(asked)
    extra = set()
    for line in simulation.splitlines():
        m = re.match(r"^(?:Remv|Purg)\s+(\S+)", line)
        if m and m.group(1) not in asked:
            extra.add(m.group(1))
    return sorted(extra)


def simulate_purge(names: list[str]) -> tuple[list[str], str]:
    """Ask apt what purging these would do. Returns (unrequested removals, error)."""
    res = run(["apt-get", "-s", "purge", "--", *names], check=False)
    if res.returncode != 0:
        return [], (res.stderr or res.stdout).strip()
    return collateral(names, res.stdout), ""


def preflight(sel: Selection, plan: Plan, dry_run: bool) -> Refusal | None:
    """Why this selection must not be applied, or None when it may."""
    if not plan.is_root and not dry_run:
        return Refusal("Permission", ["Changes need root.", "Re-run with sudo, or use --dry-run."])
    if sel.pkgs:
        extra, err = simulate_purge(sel.pkgs)
        if err:
            return Refusal("apt error", err.splitlines())
        if extra:
            return Refusal(
                "Refused: collateral removals",
                ["apt would ALSO remove packages you did not select:", ""]
                + [f"  {x}" for x in extra]
                + ["", "Usually a metapackage still depends on an old kernel.",
                   "Run: sudo apt update && sudo apt full-upgrade, then retry."],
            )
    return None


def execute(sel: Selection, dry_run: bool) -> None:
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    before = boot_free()

    def sh(cmd: list[str]) -> None:
        print("$ " + shlex.join(cmd), flush=True)
        if not dry_run:
            subprocess.run(cmd, check=True, env=env)

    if sel.pkgs:
        print("\n==> Purging old kernel packages")
        sh(["apt-get", "purge", "-y", "--", *sel.pkgs])
    if sel.rc:
        print("\n==> Purging leftover configuration")
        sh(["dpkg", "--purge", "--", *sel.rc])
    if sel.files:
        print("\n==> Deleting orphaned /boot files")
        for path in sel.files:
            print(f"$ rm {shlex.quote(path)}", flush=True)
            if not dry_run:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    print(f"  already gone: {path}")
        if shutil.which("update-grub"):
            sh(["update-grub"])

    if dry_run:
        print("\nDry run: nothing was changed.")
    else:
        after = boot_free()
        print(f"\nDone. /boot free: {human(before)} -> {human(after)} (freed {human(after - before)})")


# --------------------------------------------------------------------------- #
# TUI
# --------------------------------------------------------------------------- #
@dataclass
class Row:
    kind: str                  # section / group / item / info / blank / protected
    text: str
    key: str | None = None     # item: its selection key; group: grp:NAME
    children: list[str] = field(default_factory=list)   # group: keys of its items
    size: int = 0              # bytes
    note: str = ""             # protected: why it stays


class Tui:
    C_TITLE, C_SECTION, C_SEL, C_DIM, C_OK, C_WARN, C_ERR, C_KEY = range(1, 9)

    KEYS = [("↑↓", "move"), ("Space", "toggle"), ("a", "all"), ("n", "none"),
            ("+/-", "keep"), ("r", "rescan"), ("Enter", "apply"), ("q", "quit")]

    # Cursor-key sequences curses may not map itself: terminals send ESC [ B
    # or ESC O B depending on keypad mode, and terminfo knows only one.
    ESC_SEQ = {
        "[A": curses.KEY_UP, "OA": curses.KEY_UP,
        "[B": curses.KEY_DOWN, "OB": curses.KEY_DOWN,
        "[H": curses.KEY_HOME, "OH": curses.KEY_HOME, "[1~": curses.KEY_HOME,
        "[F": curses.KEY_END, "OF": curses.KEY_END, "[4~": curses.KEY_END,
        "[5~": curses.KEY_PPAGE, "[6~": curses.KEY_NPAGE,
    }

    def __init__(self, scr, plan: Plan, opts: Options):
        self.scr = scr
        self.plan = plan
        self.opts = opts
        self.selected: set[str] = set()
        self.rows: list[Row] = []
        self.cursor = 0
        self.top = 0
        self.status = ""
        self.init_curses()
        self.rebuild()

    # ----- setup ---------------------------------------------------------- #
    def init_curses(self) -> None:
        try:
            curses.set_escdelay(25)
        except (AttributeError, curses.error):
            pass
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.has_color = curses.has_colors()
        if not self.has_color:
            return
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(self.C_TITLE, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(self.C_SECTION, curses.COLOR_CYAN, -1)
        curses.init_pair(self.C_SEL, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(self.C_DIM, curses.COLOR_BLUE, -1)
        curses.init_pair(self.C_OK, curses.COLOR_GREEN, -1)
        curses.init_pair(self.C_WARN, curses.COLOR_YELLOW, -1)
        curses.init_pair(self.C_ERR, curses.COLOR_RED, -1)
        curses.init_pair(self.C_KEY, curses.COLOR_YELLOW, -1)

    def attr(self, pair: int, extra: int = 0) -> int:
        return (curses.color_pair(pair) if self.has_color else 0) | extra

    @classmethod
    def getkey(cls, win) -> int:
        """getch() that also decodes the escape sequences in ESC_SEQ.
        A bare ESC is returned as 27, an unknown sequence as -1."""
        ch = win.getch()
        if ch != 27:
            return ch
        win.nodelay(True)
        seq = ""
        try:
            while len(seq) < 6:
                c = win.getch()
                if c == -1 or c > 255:
                    break
                seq += chr(c)
                if seq in cls.ESC_SEQ:
                    break
        finally:
            win.nodelay(False)
        if not seq:
            return 27
        return cls.ESC_SEQ.get(seq, -1)

    # ----- model ---------------------------------------------------------- #
    @staticmethod
    def section(title: str, items: list[Row], group: str | None = None, unit: str = "") -> list[Row]:
        rows = [Row("blank", ""), Row("section", title)]
        if not items:
            return rows + [Row("info", "None.")]
        if group:
            rows.append(Row("group", f"all ({plural(len(items), unit)})", key=f"grp:{group}",
                            children=[r.key for r in items], size=sum(r.size for r in items)))
        return rows + items

    def rebuild(self) -> None:
        """Rows from the plan, with the default selection ticked."""
        p = self.plan
        rows = [Row("section", "Installed kernels")]
        newest = p.versions[0] if p.versions else None
        for v in p.protected:
            tags = [t for t, hit in (("running", v == p.running), ("newest", v == newest)) if hit]
            rows.append(Row("protected", v, note="kept: " + ", ".join(tags or ["within --keep"])))
        if not p.old:
            rows.append(Row("info", "No old kernels to remove."))
        for v, pkgs in p.old.items():
            rows.append(Row("group", f"{v}  (old, {plural(len(pkgs), 'package')})", key=f"grp:{v}",
                            children=[f"pkg:{x.name}" for x in pkgs], size=sum(x.size for x in pkgs)))
            rows += [Row("item", x.name, key=f"pkg:{x.name}", size=x.size) for x in pkgs]
        rows += [Row("protected", x.name, note="on hold") for x in p.held]

        rows += self.section("Leftover configuration of removed kernel packages",
                             [Row("item", x.name, key=f"rc:{x.name}") for x in p.kernel_rc])
        rows += self.section("Leftover configuration of other removed packages",
                             [Row("item", x.name, key=f"rc:{x.name}") for x in p.other_rc],
                             group="other-rc", unit="package")
        rows += self.section("Orphaned kernel files in /boot (owned by no package)",
                             [Row("item", path, key=f"file:{path}", size=size) for path, size in p.orphans],
                             group="orphans", unit="file")

        self.rows = rows
        self.selected = default_selection(p, self.opts)
        self.cursor = min(self.cursor, len(rows) - 1)
        if not self.selectable(self.cursor):
            self.move(1)

    def selectable(self, i: int) -> bool:
        return 0 <= i < len(self.rows) and self.rows[i].kind in ("item", "group")

    def group_state(self, row: Row) -> str:
        n = sum(1 for k in row.children if k in self.selected)
        return "x" if n == len(row.children) else ("-" if n else " ")

    def toggle(self, i: int) -> None:
        row = self.rows[i]
        if row.kind == "item":
            self.selected ^= {row.key}
        elif row.kind == "group":
            if self.group_state(row) == "x":
                self.selected -= set(row.children)
            else:
                self.selected |= set(row.children)

    def selection(self) -> Selection:
        return Selection.from_keys(self.selected)

    # ----- navigation ----------------------------------------------------- #
    def move(self, delta: int) -> None:
        i = self.cursor
        while True:
            i += delta
            if not 0 <= i < len(self.rows):
                return
            if self.selectable(i):
                self.cursor = i
                return

    def page(self, delta: int) -> None:
        self.cursor = max(0, min(len(self.rows) - 1, self.cursor + delta * self.list_height()))
        if not self.selectable(self.cursor):
            self.move(1 if delta > 0 else -1)
            if not self.selectable(self.cursor):
                self.move(-1 if delta > 0 else 1)

    def jump(self, to_end: bool) -> None:
        self.cursor = len(self.rows) - 1 if to_end else 0
        if not self.selectable(self.cursor):
            self.move(-1 if to_end else 1)

    # ----- drawing -------------------------------------------------------- #
    def list_height(self) -> int:
        return max(1, self.scr.getmaxyx()[0] - 5)

    def put(self, y: int, x: int, text: str, attr: int = 0) -> None:
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        try:
            self.scr.addstr(y, x, text[: max(0, w - x)], attr)
        except curses.error:
            pass

    def draw(self) -> None:
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        p = self.plan

        # header
        title = " Kernel Remover "
        right = f" running {p.running} | keep {p.keep} | /boot free {human(boot_free())} "
        if self.opts.dry_run:
            right = " DRY RUN |" + right
        if not p.is_root:
            right = " NOT ROOT (read-only) |" + right
        self.put(0, 0, " " * w, self.attr(self.C_TITLE))
        self.put(0, 0, title, self.attr(self.C_TITLE, curses.A_BOLD))
        self.put(0, max(len(title), w - len(right)), right, self.attr(self.C_TITLE))

        # list
        lh = self.list_height()
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + lh:
            self.top = self.cursor - lh + 1
        for i in range(self.top, min(len(self.rows), self.top + lh)):
            y = 2 + i - self.top
            row = self.rows[i]
            focused = i == self.cursor
            if row.kind == "section":
                self.put(y, 1, row.text, self.attr(self.C_SECTION, curses.A_BOLD))
                self.put(y, 2 + len(row.text), "─" * max(0, w - len(row.text) - 3), self.attr(self.C_SECTION))
            elif row.kind == "info":
                self.put(y, 5, row.text, self.attr(self.C_DIM))
            elif row.kind == "protected":
                self.put(y, 3, "🔒 " if w > 60 else "* ")
                self.put(y, 6, row.text, curses.A_BOLD)
                self.put(y, 7 + len(row.text), f"({row.note})", self.attr(self.C_OK))
            elif row.kind in ("item", "group"):
                mark = self.group_state(row) if row.kind == "group" else ("x" if row.key in self.selected else " ")
                indent = 3 if row.kind == "group" else 5
                a = self.attr(self.C_SEL) if focused else (curses.A_BOLD if row.kind == "group" else 0)
                if focused:
                    self.put(y, 0, " " * w, a)
                self.put(y, indent, f"[{mark}] {row.text}", a)
                if row.size:
                    s = human(row.size)
                    self.put(y, w - len(s) - 2, s, a if focused else self.attr(self.C_DIM))

        # scroll position
        if len(self.rows) > lh:
            self.put(1, w - 12, f"{self.top + 1}-{min(len(self.rows), self.top + lh)}/{len(self.rows)}",
                     self.attr(self.C_DIM))

        # footer
        sel = self.selection()
        summary = (f" Selected: {plural(len(sel.pkgs), 'package')}, {plural(len(sel.rc), 'config')}, "
                   f"{plural(len(sel.files), 'file')}  (~{human(p.size_of(self.selected))})")
        self.put(h - 3, 0, "─" * w, self.attr(self.C_DIM))
        self.put(h - 2, 0, summary, curses.A_BOLD)
        if self.status:
            self.put(h - 2, min(w - 1, len(summary) + 2), self.status, self.attr(self.C_WARN))
        x = 1
        for key, label in self.KEYS:
            self.put(h - 1, x, key, self.attr(self.C_KEY, curses.A_BOLD))
            x += len(key)
            self.put(h - 1, x, f" {label}  ")
            x += len(label) + 3
        scr.refresh()

    # ----- dialogs -------------------------------------------------------- #
    def dialog(self, title: str, lines: list[str], footer: str, pair: int) -> int:
        h, w = self.scr.getmaxyx()
        width = min(w - 4, max(len(title) + 4, len(footer) + 4, max((len(x) for x in lines), default=0) + 4))
        height = min(h - 2, len(lines) + 4)
        win = curses.newwin(height, width, max(0, (h - height) // 2), max(0, (w - width) // 2))
        win.erase()
        win.box()
        try:
            win.addstr(0, 2, f" {title} ", self.attr(pair, curses.A_BOLD))
            for i, line in enumerate(lines[: height - 4]):
                win.addstr(1 + i, 2, line[: width - 4])
            win.addstr(height - 2, max(1, (width - len(footer)) // 2), footer, self.attr(self.C_KEY, curses.A_BOLD))
        except curses.error:
            pass
        win.keypad(True)
        win.refresh()
        return self.getkey(win)

    def confirm_apply(self) -> bool:
        sel = self.selection()
        if sel.empty():
            self.status = "Nothing selected."
            return False

        self.status = "Checking with apt..."
        self.draw()
        refusal = preflight(sel, self.plan, self.opts.dry_run)
        self.status = ""
        if refusal:
            self.dialog(refusal.title, refusal.lines, "[any key] back", self.C_ERR)
            return False

        lines = []
        if sel.pkgs:
            size = human(self.plan.size_of(f"pkg:{n}" for n in sel.pkgs))
            lines.append(f"Purge {plural(len(sel.pkgs), 'kernel package')}  (~{size})")
        if sel.rc:
            lines.append(f"Purge leftover configuration of {plural(len(sel.rc), 'package')}")
        if sel.files:
            size = human(self.plan.size_of(f"file:{f}" for f in sel.files))
            lines.append(f"Delete {plural(len(sel.files), 'orphaned /boot file')}  (~{size})")
        lines += ["", "Kept kernels: " + ", ".join(self.plan.protected)]
        if self.opts.dry_run:
            lines += ["", "DRY RUN: commands will only be printed."]
        ch = self.dialog("Confirm", lines, "[Enter/y] apply    [Esc/n] back", self.C_WARN)
        return ch in (10, 13, ord("y"), ord("Y"))

    # ----- main loop ------------------------------------------------------ #
    def loop(self) -> Selection | None:
        while True:
            self.draw()
            ch = self.getkey(self.scr)
            self.status = ""
            if ch in (ord("q"), ord("Q"), 27):
                return None
            elif ch in (curses.KEY_UP, ord("k")):
                self.move(-1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                self.move(1)
            elif ch == curses.KEY_PPAGE:
                self.page(-1)
            elif ch == curses.KEY_NPAGE:
                self.page(1)
            elif ch in (curses.KEY_HOME, ord("g")):
                self.jump(False)
            elif ch in (curses.KEY_END, ord("G")):
                self.jump(True)
            elif ch == ord(" "):
                if self.selectable(self.cursor):
                    self.toggle(self.cursor)
            elif ch == ord("a"):
                self.selected = {r.key for r in self.rows if r.kind == "item"}
            elif ch == ord("n"):
                self.selected = set()
            elif ch in (ord("+"), ord("=")):
                self.rescan(self.plan.keep + 1)
            elif ch == ord("-"):
                if self.plan.keep > 1:
                    self.rescan(self.plan.keep - 1)
                else:
                    self.status = "keep cannot go below 1"
            elif ch == ord("r"):
                self.rescan(self.plan.keep)
            elif ch in (10, 13, curses.KEY_ENTER):
                if self.confirm_apply():
                    return self.selection()
            elif ch == curses.KEY_RESIZE:
                self.scr.clear()

    def rescan(self, keep: int) -> None:
        self.status = "Scanning..."
        self.draw()
        self.plan = build_plan(read_host(), keep)
        self.rebuild()
        self.status = f"Rescanned with keep={keep}."


def run_tui(plan: Plan, opts: Options) -> Selection | None:
    return curses.wrapper(lambda scr: Tui(scr, plan, opts).loop())


# --------------------------------------------------------------------------- #
# plain mode
# --------------------------------------------------------------------------- #
def listing(title: str, names: list[str]) -> None:
    if names:
        print(f"\n{title}")
        for name in names:
            print(f"  {name}")


def plain_mode(plan: Plan, opts: Options) -> int:
    print(f"Running kernel : {plan.running}")
    print(f"Keeping        : {', '.join(plan.protected)}  (--keep {plan.keep} + running)")
    print(f"/boot free     : {human(boot_free())}")

    if plan.old:
        listing("Old kernel packages to purge:", [x.name for pkgs in plan.old.values() for x in pkgs])
    else:
        print("\nNo old kernel packages to remove.")
    listing("Skipped (on hold):", [x.name for x in plan.held])
    listing("Leftover kernel configuration to purge:", [x.name for x in plan.kernel_rc])
    if plan.other_rc and opts.all_rc:
        listing("Other leftover configuration to purge:", [x.name for x in plan.other_rc])
    elif plan.other_rc:
        print(f"\nLeftover configuration of {plural(len(plan.other_rc), 'other package')} "
              "not included (use --all-rc).")
    if plan.orphans:
        print("\nOrphaned /boot files" + (" to delete:" if opts.purge_orphans else " (use --purge-orphans to delete):"))
        for path, size in plan.orphans:
            print(f"  {path}  ({human(size)})")
    print()

    sel = Selection.from_keys(default_selection(plan, opts))
    if sel.empty():
        print("Nothing to do.")
        return 0
    refusal = preflight(sel, plan, opts.dry_run)
    if refusal:
        die("\n".join([refusal.title, *refusal.lines]))
    if sel.pkgs:
        print("apt simulation OK: no collateral removals.")
    if not opts.yes and not opts.dry_run:
        try:
            if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return 0
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 0
    execute(sel, opts.dry_run)
    return 0


# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> Options:
    ap = argparse.ArgumentParser(
        prog=os.environ.get("SNAP_NAME"),
        description="Safely remove old Ubuntu kernels and their leftovers.",
    )
    ap.add_argument("-k", "--keep", type=int, default=DEFAULT_KEEP, metavar="N",
                    help=f"newest kernel versions to keep (default {DEFAULT_KEEP}); the running kernel is always kept")
    ap.add_argument("-n", "--dry-run", action="store_true", help="only print the commands that would run")
    ap.add_argument("-p", "--plain", action="store_true", help="no TUI; plain text mode")
    ap.add_argument("-y", "--yes", action="store_true", help="plain mode: do not ask for confirmation")
    ap.add_argument("--all-rc", action="store_true", help="also select leftover configuration of non-kernel packages")
    ap.add_argument("--purge-orphans", action="store_true", help="also select orphaned /boot files")
    a = ap.parse_args(argv)
    if a.keep < 1:
        ap.error("--keep must be at least 1")
    return Options(keep=a.keep, dry_run=a.dry_run, plain=a.plain, yes=a.yes,
                   all_rc=a.all_rc, purge_orphans=a.purge_orphans)


def main(argv: list[str] | None = None) -> int:
    opts = parse_args(argv)
    for tool in ("apt-get", "apt-mark", "dpkg", "dpkg-query"):
        if not shutil.which(tool):
            die(f"{tool} not found; this tool is for Debian/Ubuntu systems")

    plan = build_plan(read_host(), opts.keep)
    if opts.plain or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return plain_mode(plan, opts)

    sel = run_tui(plan, opts)
    if sel is None:
        print("Aborted, nothing changed.")
        return 0
    execute(sel, opts.dry_run)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except subprocess.CalledProcessError as e:
        die(f"command failed with exit code {e.returncode}: {shlex.join(e.cmd)}", e.returncode)
