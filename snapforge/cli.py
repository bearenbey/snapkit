"""Argument handling, and the command each argument leads to."""

import argparse
import subprocess
import sys
from pathlib import Path

from . import adopt, github, local, project, snapdb, sources, update
from .db import Database, NameTaken
from .net import NetworkError
from .report import PlainReporter

USAGE = """snapkit -- make a snap package out of a GitHub repository or a
package file you already have, and keep it up to date afterwards.

  snapkit                          the dashboard
  snapkit create <repo>            make a snap from a repository
  snapkit create ./thing.deb       ... or from a .deb, archive or AppImage
  snapkit create ~/Downloads       ... or from whichever of those is in there
  snapkit create                   ... and with nothing named, it asks
  snapkit package <name|repo>      build one already registered, from the register
  snapkit search <text>            find a registered snap by name, repo or summary
  snapkit list                     what is registered
  snapkit show <name>              one record, in full
  snapkit check [name ...]         what has a newer release upstream
  snapkit update <name> [...]      move a snap onto that release
  snapkit build <name>             hand the project to snapcraft
  snapkit remove <name>            forget a snap, and its recipe with it
  snapkit db                       what the shared recipe database holds
  snapkit db pull [name ...]       write those projects here, or all of them
  snapkit install <name>           fetch it, build it, and offer to install it

A repository can be given any way you have it: owner/name, the browser URL,
the clone URL, or a link to a release page. A repository that has been used
before is recognised, and `package` will build it again from the register
without going upstream at all -- so the second time round it is a name rather
than a URL.

`snapkit db` reads a folder of recipes published in a git repository, so a
project somebody else packaged can be built here without being packaged again.
`install` is the whole of it in one command: fetch, build, and ask before
anything touches the system.

Not everything is published as a GitHub release, so not everything has to be
given as one. Point `create` at a file instead and the file is what gets
packaged: it is copied in beside the recipe that names it, and the snap is
then tracked against that folder rather than against an upstream -- drop a
newer one in and `snapkit check` says so.
"""


# How many to offer before the list is worse than the question.
CHOICES = 9


def can_ask(args):
    """Whether there is a person at the other end to put a question to."""
    return sys.stdin.isatty() and sys.stdout.isatty() and not args.plain


def ask_yes_no(question, default=False):
    """A yes/no on the terminal, with no as the default."""
    suffix = "[y/N]" if not default else "[Y/n]"
    try:
        answer = input(f"{question} {suffix} ").strip().lower()
    except EOFError:
        return default
    return default if not answer else answer.startswith("y")


def die(message):
    print(f"snapkit: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="snapkit", add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter, description=USAGE)
    parser.add_argument("command", nargs="?", default="")
    parser.add_argument("rest", nargs="*", metavar="...")
    parser.add_argument("--name", help="what to call the snap, if not the repository name")
    parser.add_argument("--tag", help="a release to use instead of the newest")
    parser.add_argument("--asset", help="the release file to build from, by name "
                                        "or by its number in the list")
    parser.add_argument("--repo", help="on import, the upstream repository when "
                                       "the project does not say")
    parser.add_argument("--dir", dest="directory", help="where to write the project")
    parser.add_argument("--no-build", action="store_true",
                        help="write the project but do not run snapcraft")
    parser.add_argument("--yes", action="store_true", help="do not ask before removing")
    parser.add_argument("--force", action="store_true",
                        help="on update, redo a project that is already current")
    parser.add_argument("--local", action="store_true",
                        help="on create, treat what was given as a package file "
                             "or a folder to look in, never as a repository")
    parser.add_argument("--plain", action="store_true",
                        help="never take over the terminal, even on a tty")
    parser.add_argument("-h", "--help", action="help", help="this")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    db = Database()
    reporter = PlainReporter()
    for record, why in db.problems:
        # Loud and on every command: one record short beats failing to open.
        reporter.warn(f"{record} could not be read and was left out: {why}")

    interactive = can_ask(args) and not args.command
    if interactive:
        # Imported here: `snapkit check` in cron should not pay for rich.
        from .tui import run_dashboard
        return run_dashboard(db)

    handlers = {"": cmd_list, "create": cmd_create, "list": cmd_list,
                "show": cmd_show, "check": cmd_check, "update": cmd_update,
                "build": cmd_build, "remove": cmd_remove, "rm": cmd_remove,
                "search": cmd_search, "find": cmd_search, "package": cmd_package,
                "import": cmd_import, "adopt": cmd_import,
                "db": cmd_db, "install": cmd_install}
    handler = handlers.get(args.command)
    if handler is None:
        die(f"no such command: {args.command} (try --help)")
    try:
        return handler(db, args, reporter)
    except (project.ForgeError, NetworkError, github.NotFound,
            snapdb.DatabaseError) as exc:
        die(str(exc))
    except ValueError as exc:
        die(str(exc))
    except KeyError as exc:
        die(exc.args[0])


# -- the commands -------------------------------------------------------------

def cmd_create(db, args, reporter):
    text = " ".join(args.rest).strip()
    if not text:
        text = ask_what_to_package(db, args)
    if args.local or local.looks_like_path(text):
        return create_from_file(db, args, reporter, text)
    known = db.find_repo(github.parse_repo(text))
    if known and not args.name:
        # Been here before, so build it from the record rather than the URL.
        reporter.detail(f"{known.repo} is already registered as {known.name} "
                        f"({known.version}) -- building it from the register")
        reporter.detail(f"(--name makes a second one; `snapkit update "
                        f"{known.name}` looks for a newer release)")
        project.package(known, reporter, build_it=not args.no_build)
        db.add(known)
        return 0

    made = project.plan(text, reporter, tag=args.tag, name=args.name,
                        asset=args.asset)
    if len(made.candidates) > 1 and not args.asset:
        reporter.detail("the rest of this release, if that is the wrong file "
                        "(--asset takes a name or a number):")
        for number, candidate in enumerate(made.candidates[1:6], 2):
            reporter.detail(f"  {number}. {candidate.name}")
    return _finish_create(db, args, reporter, made, text)


def create_from_file(db, args, reporter, text):
    """Make a snap out of a package file, rather than out of a release."""
    path = Path(text).expanduser()
    known = _registered_at(db, path if path.is_dir() else path.parent)
    if known and not args.name:
        reporter.detail(f"{path if path.is_dir() else path.parent} is already "
                        f"registered as {known.name} ({known.version})")
        reporter.detail(f"treating this as an update; `--name` would make a "
                        f"second snap from it instead")
        return update_one(db, args, reporter, known)

    made = project.plan_local(path, reporter, name=args.name, asset=args.asset)
    if len(made.candidates) > 1 and not args.asset:
        reporter.detail("the rest of what is in there, if that is the wrong "
                        "one (--asset takes a name or a number):")
        for number, candidate in enumerate(made.candidates[1:6], 2):
            reporter.detail(f"  {number}. {candidate.name}")
    return _finish_create(db, args, reporter, made, text)


def _finish_create(db, args, reporter, made, text):
    """Register what a plan produced, and build it unless told not to."""
    snap = project.create(made, reporter, directory=args.directory)
    try:
        db.add(snap)
    except NameTaken as exc:
        die(f"{exc}\n           try: snapkit create {text} "
            f"--name {db.free_name(snap.name)}")
    reporter.result(f"registered {snap.name} {snap.version}")
    if snap.upstream.get("kind") == "local":
        reporter.detail(f"tracked against {snap.path}: drop a newer "
                        f"{snap.asset_glob} in and `snapkit check` will say so")
    if args.no_build:
        reporter.detail(f"build it with: cd {snap.path} && "
                        f"{snap.build_with or 'snapcraft'}")
        return 0
    project.build(snap, reporter)
    db.add(snap)
    return 0


def _registered_at(db, directory):
    """The snap whose project directory this is, if it is one."""
    try:
        wanted = Path(directory).expanduser().resolve()
    except OSError:
        return None
    for snap in db:
        if snap.directory and Path(snap.directory).resolve() == wanted:
            return snap
    return None


def ask_what_to_package(db, args):
    """With nothing named: what is in this folder, or a repository."""
    here = Path(args.directory).expanduser() if args.directory else Path.cwd()
    found = local.find(here)
    if not can_ask(args):
        _no_prompt_help(here, found)
    _show_choices(here, found)
    return _read_choice(found)


def _no_prompt_help(here, found):
    """What to say instead of asking, when there is nobody to ask."""
    if found:
        die("create needs something to make a snap from.\n\n"
            f"           These are in {here}:\n"
            + "\n".join(f"             {f.name}" for f in found[:6])
            + "\n\n           Package one of them, or name a repository:\n"
            f"             snapkit create ./{found[0].name}\n"
            "             snapkit create owner/name")
    die("create needs a repository or a package file:\n"
        "           snapkit create owner/name\n"
        "           snapkit create ./something.deb\n"
        "           snapkit create ~/Downloads")


def _show_choices(here, found):
    """The menu: what is in the folder, and the two other ways to answer."""
    print("What should this snap be made from?\n")
    if found:
        print(f"  packages in {here}")
        width = max(len(one.name) for one in found[:CHOICES])
        for number, one in enumerate(found[:CHOICES], 1):
            version = f" {one.version}" if one.version else ""
            print(f"    {number}. {one.name:<{width}}{version}   {one.why}")
        print()
        print(f"  [1-{min(len(found), CHOICES)}]  package that file")
    else:
        print(f"  no package file in {here}")
    print("  [r]    a GitHub repository -- owner/name, or a URL")
    print("  [p]    a path to a package file, or a folder to look in")
    print("  [q]    nothing\n")


def _read_choice(found):
    """One answer to that menu, as the text `create` should act on."""
    while True:
        answer = input("> ").strip()
        low = answer.lower()
        if low in ("q", "quit", ""):
            raise SystemExit(0)
        if answer.isdigit() and 1 <= int(answer) <= min(len(found), CHOICES):
            return str(found[int(answer) - 1].path)
        if low in ("r", "repo", "repository"):
            typed = input("repository> ").strip()
            if typed:
                return typed
        elif low in ("p", "path", "file"):
            typed = input("path> ").strip()
            if typed:
                return typed
        else:
            # Anything else is the answer: typing a repository in means it.
            return answer


def cmd_search(db, args, reporter):
    """Find something already registered, by whatever part of it is remembered."""
    if not args.rest:
        die("search needs something to look for")
    found = db.search(" ".join(args.rest))
    if not found:
        print(f"nothing registered matches {' '.join(args.rest)!r}")
        return 1
    print_table(found)
    print(f"\nbuild one with: snapkit package {found[0].name}")
    return 0


def cmd_package(db, args, reporter):
    """Build something already registered, from the register alone."""
    if not args.rest:
        die("package needs a name, a repository, or something to search for")
    snap = _one_of(db, " ".join(args.rest))
    project.package(snap, reporter, build_it=not args.no_build)
    db.add(snap)
    return 0


def _one_of(db, text):
    """The single registered snap `text` refers to, or a helpful refusal."""
    if text in db:
        return db.get(text)
    found = db.search(text)
    if not found:
        die(f"nothing registered matches {text!r} -- "
            f"`snapkit create {text}` would make it")
    if len(found) > 1 and found[0].name.lower() != text.strip().lower():
        die(f"{text!r} matches {len(found)}: "
            + ", ".join(snap.name for snap in found[:6])
            + "\n           name the one you mean")
    return found[0]


def cmd_import(db, args, reporter):
    """Register snap projects that are already on disk."""
    if not args.rest:
        die("import needs a directory: snapkit import ../btop-snap")
    taken = skipped = 0
    for given in args.rest:
        directory = Path(given)
        try:
            snap, recipe, is_snapcraft, confirmed = adopt.read(
                directory, repo=args.repo)
        except adopt.NotAProject as exc:
            reporter.warn(str(exc))
            skipped += 1
            continue

        existing = db.snaps.get(snap.name)
        if existing and existing.directory != snap.directory:
            reporter.warn(f"{snap.name} is already registered from "
                          f"{existing.directory}; leaving it alone")
            skipped += 1
            continue

        icon = adopt.find_icon(directory)
        if icon:
            snap.icon = f"snap/gui/{snap.name}{icon.suffix}"
            snap.keep_icon(icon)
        reporter.step(f"{snap.name} {snap.version}  ({recipe})")
        if not confirmed and not snap.upstream:
            track_locally(snap, args, reporter)
        for note in adopt.reasons(snap, is_snapcraft, confirmed):
            reporter.detail(note)
        db.add(snap, replace=True)
        taken += 1
    print(f"\nregistered {taken}" + (f", skipped {skipped}" if skipped else ""))
    return 0 if taken else 1


def track_locally(snap, args, reporter):
    """Offer to keep an unconfirmed project in step with its own folder."""
    found = local.find(snap.path, snap.asset_glob or None)
    if not found:
        return False
    best = local.newest(snap.path, snap.asset_glob or None)
    glob = snap.asset_glob or local.glob_for(best.name, best.version)

    if not args.local:
        if not can_ask(args):
            reporter.detail(f"{best.name} is here; `--repo owner/name` tracks a "
                            f"repository, `--local` tracks this folder")
            return False
        print(f"\n  {snap.name} does not say which repository it packages, but "
              f"{best.name}\n  is in its folder.")
        print(f"  Watching the folder means: drop a newer {glob} in, and "
              f"`snapkit check`\n  reports it. Nothing is claimed about where "
              f"the file came from.")
        if input(f"  Track {snap.name} against its folder? [y/N] ").strip().lower() \
                not in ("y", "yes"):
            return False

    snap.style = "artifact"
    snap.asset = snap.asset or best.name
    snap.asset_glob = glob
    snap.upstream = {"kind": "local", "glob": glob}
    return True


def cmd_list(db, args, reporter):
    if not db:
        print("nothing registered yet -- snapkit create owner/name")
        return 0
    print_table(db)
    return 0


def print_table(snaps):
    """The one listing, so `list` and `search` cannot drift apart."""
    print(f"{'NAME':<20} {'VERSION':<16} {'KIND':<9} UPSTREAM")
    for snap in snaps:
        print(f"{snap.name:<20} {snap.version:<16} {snap.kind:<9} "
              f"{upstream_of(snap)}")


def upstream_of(snap):
    """Where this snap's releases come from, for the listing."""
    return sources.label(snap, folder="its own folder") or "-- not tracked"


def cmd_show(db, args, reporter):
    if not args.rest:
        die("show needs a name")
    snap = db.get(args.rest[0])
    for key, value in snap.to_dict().items():
        if key == "snapcraft_yaml":
            continue
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value) or "(none)"
        print(f"{key:>16}  {value}")
    print(f"\n--- snap/snapcraft.yaml ---\n{snap.snapcraft_yaml}")
    return 0


def targets_of(db, args):
    """The snaps a command applies to: the ones named, or all of them."""
    return [db.get(name) for name in args.rest] if args.rest else db.all()


def cmd_check(db, args, reporter):
    targets = targets_of(db, args)
    if not targets:
        print("nothing registered yet")
        return 0
    print(f"{'NAME':<20} {'BUILT':<16} {'UPSTREAM':<16} STATUS")
    for snap in targets:
        found = update.situation(snap)
        upstream = found.latest or {"untracked": "-"}.get(found.state, "?")
        status = found.words
        if found.behind:
            status += f" ({found.asset.name})"
        elif found.state == "error":
            status = found.problem
        print(f"{snap.name:<20} {snap.version:<16} {upstream:<16} {status}")
        if found.note:
            print(f"{'':<20} note: {found.note}")
    return 0


def cmd_update(db, args, reporter):
    targets = targets_of(db, args)
    if not targets:
        die("update needs a name, or register something first")
    failed = []
    for snap in targets:
        try:
            update_one(db, args, reporter, snap)
        except (NetworkError, project.ForgeError) as exc:
            reporter.warn(f"{snap.name}: {exc}")
            failed.append(snap.name)
    if failed:
        print(f"snapkit: not updated: {' '.join(failed)}", file=sys.stderr)
        return 1
    return 0


def update_one(db, args, reporter, snap):
    """Move one registered snap onto whatever its upstream has now."""
    found = update.situation(snap, args.force)
    if found.state == "untracked":
        reporter.detail(found.problem)
        return 0
    if found.state == "error":
        raise project.ForgeError(found.problem)
    if found.note:
        reporter.warn(f"{snap.name}: {found.note}")
    if not found.behind:
        reporter.detail(f"{snap.name} is already at {snap.version}")
        return 0
    project.adopt(snap, reporter)
    update.update(snap, found.release, found.asset, reporter)
    db.add(snap)
    if not args.no_build:
        project.build(snap, reporter)
        db.add(snap)
    return 0


def cmd_build(db, args, reporter):
    if not args.rest:
        die("build needs a name")
    snap = db.get(args.rest[0])
    project.adopt(snap, reporter)
    project.build(snap, reporter)
    db.add(snap)
    return 0


def cmd_db(db, args, reporter):
    """What the shared database holds, and pulling projects out of it."""
    action = args.rest[0] if args.rest else "list"
    rest = args.rest[1:]

    if action == "publish":
        where = Path(rest[0]) if rest else Path.cwd() / snapdb.FOLDER
        reporter.step(f"writing {where}")
        index, left_out = snapdb.publish(db.all(), where, reporter)
        reporter.result(f"published {len(index['snaps'])} snaps to {where}")
        for name, files in left_out.items():
            reporter.warn(f"{name}: published without {', '.join(files)}, "
                          f"so it cannot be built from the database")
        return 0

    if action not in ("list", "pull"):
        # `snapkit db btop` reads as a name, not as a mistyped subcommand.
        rest, action = [action, *rest], "pull"

    found = snapdb.index()
    snaps = found["snaps"]

    if action == "list":
        reporter.detail(f"{snapdb.base_url()}")
        width = max((len(n) for n in snaps), default=4)
        drifted, unpublished = [], []
        for name in sorted(snaps):
            published = snaps[name]
            here = db.snaps.get(name)
            mark = " "
            if here:
                mark = "*"
                if Path(here.path).is_dir():
                    if snapdb.local_fingerprint(here.path) != published.get("fingerprint"):
                        mark, _ = "~", drifted.append(name)
            reporter.detail(f"{mark} {name:<{width}}  {published['version']:<16}"
                            f"  {published.get('summary', '')[:42]}")
        for name in sorted(db.snaps):
            if name not in snaps:
                unpublished.append(name)
                reporter.detail(f"+ {name:<{width}}  {db.snaps[name].version:<16}"
                                f"  not in the database")
        reporter.detail("")
        reporter.detail("*  registered here    ~  differs from the database"
                        "    +  here but not published")
        if drifted or unpublished:
            reporter.warn(f"{len(drifted) + len(unpublished)} project(s) have "
                          f"moved on: snapkit db publish <dir> writes them out")
        else:
            reporter.detail("the database matches the projects here")
        return 0

    wanted = rest or sorted(snaps)
    where = Path(args.directory) if args.directory else Path.cwd()
    done, failed = 0, []
    for name in wanted:
        target = where / f"{name}-snap"
        reporter.step(f"{name} -> {target}")
        try:
            snapdb.fetch(name, target, found)
        except snapdb.DatabaseError as exc:
            # One bad snap should not stop the rest; named outright, it does.
            if rest:
                raise
            reporter.warn(str(exc))
            failed.append(name)
            continue
        done += 1
    reporter.result(f"pulled {done} of {len(wanted)}")
    if failed:
        reporter.detail(f"not pulled: {', '.join(failed)}")
    return 0


def cmd_install(db, args, reporter):
    """Fetch a snap from the database, build it, and offer to install it."""
    if not args.rest:
        die("install needs a name")
    name = args.rest[0]

    snap = db.snaps.get(name)
    if snap is None:
        where = Path(args.directory) if args.directory else Path.cwd()
        target = where / f"{name}-snap"
        reporter.step(f"fetching {name} from the database")
        snapdb.fetch(name, target, reporter=reporter)
        try:
            snap, recipe, is_snapcraft, confirmed = adopt.read(target)
        except adopt.NotAProject as exc:
            die(f"{name} was fetched but does not read as a project: {exc}")
        icon = adopt.find_icon(target)
        if icon:
            snap.icon = f"snap/gui/{snap.name}{icon.suffix}"
            snap.keep_icon(icon)
        snapdb.apply_record(snap, snapdb.entry(name))
        reporter.detail(f"{snap.name} {snap.version}  ({recipe})")
        db.add(snap, replace=True)
    else:
        reporter.detail(f"{name} is already registered here")

    # A fetched project has no release yet, so go and get it before building.
    if update.missing_artifact(snap):
        reporter.step(f"fetching the release {name} builds from")
        update_one(db, args, reporter, snap)
        snap = db.get(name)

    if args.no_build:
        reporter.result(f"{name} is ready in {snap.path}")
        return 0

    project.adopt(snap, reporter)
    built = project.build(snap, reporter)
    db.add(snap)
    if not can_ask(args):
        reporter.detail(f"install it with: sudo snap install --dangerous {built}")
        return 0
    if not ask_yes_no(f"install {built.name}?"):
        reporter.detail(f"built {built.name}, not installed")
        return 0

    classic = "confinement: classic" in (Path(snap.path) / "snap"
                                         / "snapcraft.yaml").read_text()
    command = ["sudo", "snap", "install", "--dangerous",
               *(["--classic"] if classic else []), str(built)]
    reporter.step(" ".join(command))
    return subprocess.run(command).returncode


def cmd_remove(db, args, reporter):
    if not args.rest:
        die("remove needs a name")
    snap = db.get(args.rest[0])
    if not args.yes:
        print(f"This forgets {snap.name} ({snap.repo}) and the snapcraft.yaml "
              f"stored with it.")
        print(f"The project directory {snap.path} is left alone.")
        if input("Remove it? [y/N] ").strip().lower() not in ("y", "yes"):
            print("left alone")
            return 1
    db.remove(snap.name)
    print(f"removed {snap.name}")
    return 0


def entry():
    """The console script the snap installs, so Ctrl-C is not a traceback."""
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        raise SystemExit(130)
