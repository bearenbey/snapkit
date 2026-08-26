"""Turn a GitHub repository into a snap package, and keep it that way.

You give it a repository. It finds what that repository publishes, works out
what shape the release is in, writes a snapcraft.yaml around it, builds the
snap, and remembers the whole thing so that the next release is one keystroke
rather than the same afternoon over again.

  net         HTTP, downloads, checksums
  github      a repository URL -> its newest release and what is attached to it
  classify    an asset list -> what kind of thing upstream actually ships
  recipe      that, plus metadata -> a snapcraft.yaml
  db          the register every created snap is written into
  project     writing a project directory out of a record, and building it
  cli         argument handling
  keys        single keystrokes, out of a terminal
  tui         the dashboard

The package is deliberately self-contained: it is what ships inside the snap,
so it borrows nothing from the directory it happens to live in.
"""

__all__ = ["adopt", "classify", "cli", "db", "github", "keys", "net",
           "project", "recipe", "report", "tui"]
