# What this trusts

Worth saying plainly, because packaging software means running it.

**Building a project runs code that came from somewhere else.** `pack.py` is
imported and called, and 14 of the 25 published projects have one. `snapkit
install <name>` fetches a project from the database and builds it, so it runs
that project's `pack.py` on your machine. The file is in the project you can
read, and its sha256 is in the index, but it is code and it runs. Read a
project before installing it if you did not write it.

**Opening an AppImage runs it.** An AppImage is the only shape that knows how
to unpack itself, so `create` marks it executable and runs it with
`--appimage-extract`. That happens during `create`, before anything is built.

**A checksum is not a signature.** Downloads are checked against the sha256
the same host published, which catches corruption and a broken mirror, not a
host that has been taken over. `track ... verify` adds a gpg check on top: a
signature gpg reads and calls bad now aborts the update and deletes the file,
but a release key you do not hold cannot say either way and only warns.

**The snap is classic.** It builds snaps and writes project directories
wherever you keep them, and neither is something a confined snap can do, so
it runs unconfined with your privileges.

**Nothing here needs root** until the last step. Installing is the one thing
that does, it is always offered rather than assumed, and it uses `snap install
--dangerous` because a snap you just built is unsigned.

---

[Back to the README](../README.md)
