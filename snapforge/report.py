"""Where the running commentary goes."""

import sys
from contextlib import contextmanager


class Reporter:
    """No-op base: a run that says nothing."""

    # Pipe a subprocess's output through `output` instead of the terminal.
    captures_output = False

    def step(self, text):
        """A phase of the work started."""

    def detail(self, text):
        """Something worth knowing about the phase in progress."""

    def progress(self, done, total):
        """Bytes of the current download."""

    def warn(self, text):
        """Something went wrong but the run continues."""

    def result(self, text):
        """A piece of work finished."""

    def output(self, line):
        """One line a subprocess wrote."""

    @contextmanager
    def suspended(self):
        """Hand the terminal back for a subprocess that writes to it."""
        yield


def colour(code, text, stream=None):
    """Wrap text in an ANSI code, or leave it alone off a terminal."""
    stream = stream or sys.stdout
    return f"\033[{code}m{text}\033[0m" if stream.isatty() else text


class PlainReporter(Reporter):
    """A line per step, for a terminal or a pipe."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self._bar_open = False

    def _print(self, text=""):
        self._end_bar()
        print(text, file=self.stream, flush=True)

    def step(self, text):
        self._print(f"{colour(36, '==>', self.stream)} {text}")

    def detail(self, text):
        self._print(f"    {text}")

    def warn(self, text):
        self._end_bar()
        print(f"{colour(33, 'warning:', sys.stderr)} {text}",
              file=sys.stderr, flush=True)

    def result(self, text):
        self._print(f"{colour(32, '==>', self.stream)} {text}")

    def output(self, line):
        # Verbatim: on a terminal this is what the build wrote there anyway.
        self._print(line)

    def progress(self, done, total):
        if not self.stream.isatty():
            return
        if total:
            share = done / total
            width = 32
            bar = "#" * int(width * share) + "-" * (width - int(width * share))
            text = (f"    [{bar}] {share * 100:5.1f}%  "
                    f"{done / 1e6:.1f}/{total / 1e6:.1f} MB")
        else:
            text = f"    {done / 1e6:.1f} MB"
        print("\r" + text, end="", file=self.stream, flush=True)
        self._bar_open = True

    def _end_bar(self):
        if self._bar_open:
            print(file=self.stream, flush=True)
            self._bar_open = False
