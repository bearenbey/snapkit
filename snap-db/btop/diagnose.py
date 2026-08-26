#!/usr/bin/env python3
"""Reinstall the btop snap, connect its interfaces, and capture everything"""

import os
import subprocess
import sys
from pathlib import Path



def yaml_version(path):
    """The `version:` field of a snapcraft.yaml."""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return ""

HERE = Path(__file__).resolve().parent
LOG = HERE / "diagnose.log"

INTERFACES = ("system-observe", "process-control", "hardware-observe",
              "mount-observe", "network-observe", "removable-media")


def say(title):
    line = f"\n===== {title} =====\n"
    print(line, end="", flush=True)
    with LOG.open("a") as log:
        log.write(line)


def record(*command, check=False, shell_input=None):
    """Run a command, showing its output and appending it to the report."""
    done = subprocess.run([str(c) for c in command], capture_output=True, text=True)
    output = (done.stdout + done.stderr).rstrip()
    if output:
        print(output, flush=True)
        with LOG.open("a") as log:
            log.write(output + "\n")
    if check and done.returncode != 0:
        note(f"{command[0]} exited with status {done.returncode}")
    return done


def note(text):
    print(text, flush=True)
    with LOG.open("a") as log:
        log.write(text + "\n")


def main():
    os.chdir(HERE)
    LOG.write_text("")

    snap = f"btop_{yaml_version(HERE / 'snap' / 'snapcraft.yaml')}_amd64.snap"
    if not (HERE / snap).is_file():
        print(f"{snap} is not here -- run snapkit build btop first", file=sys.stderr)
        return 1

    say("install")
    subprocess.run(["sudo", "snap", "remove", "--purge", "btop"],
                   capture_output=True)
    record("sudo", "snap", "install", "--dangerous", f"./{snap}")

    say("connect interfaces")
    for interface in INTERFACES:
        done = record("sudo", "snap", "connect", f"btop:{interface}")
        if done.returncode != 0:
            note(f"connect {interface} FAILED")
    record("snap", "connections", "btop")

    say("mount profile")
    record("sudo", "cat", "/var/lib/snapd/mount/snap.btop.fstab")

    say("run btop (quit with q after a few seconds)")
    # --debug logs every stage, so the last line names the one that died.
    done = subprocess.run(["snap", "run", "btop", "--debug"])
    if done.returncode != 0:
        note(f"btop exited with status {done.returncode}")

    say("btop's own log")
    own = Path.home() / "snap/btop/current/.local/state/btop.log"
    note(own.read_text().rstrip() if own.is_file() else "no btop.log")

    say("kernel: segfault / apparmor denials for btop")
    # Discord floods the audit log and buries the records worth reading.
    journal = subprocess.run(
        ["sudo", "journalctl", "--since", "-5 min", "--no-pager"],
        capture_output=True, text=True)
    wanted = [line for line in (journal.stdout + journal.stderr).splitlines()
              if "discord" not in line
              and any(word in line.lower() for word in
                      ("segfault", "snap.btop", "snap-confine", "snap-update-ns"))]
    note("\n".join(wanted[-40:]))

    say("done")
    print(f"Full report: {LOG.name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
