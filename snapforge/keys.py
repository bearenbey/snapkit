"""Reading single keystrokes from a terminal.

Separate from the dashboard because it is not about the dashboard: it is
about what a terminal sends and how easy that is to get wrong. The whole of
it is exercised by the tests without a terminal, which is the other reason it
is here rather than in the middle of five hundred lines of drawing code.
"""

import os
import re
import select
import sys
import termios
import tty

# Both forms: application mode sends ESC O A, normal mode ESC [ A.
SEQUENCES = {
    "[A": "up", "[B": "down", "[C": "right", "[D": "left",
    "OA": "up", "OB": "down", "OC": "right", "OD": "left",
    "[H": "home", "[F": "end", "[1~": "home", "[4~": "end",
    "[7~": "home", "[8~": "end", "[5~": "pageup", "[6~": "pagedown",
    "[3~": "delete", "[Z": "backtab",
}

_SEQUENCE = re.compile(r"[\[O][0-9;]*[A-Za-z~]")


class Keyboard:
    """Single keystrokes, without waiting for a line.

    Read straight from the file descriptor rather than through sys.stdin.
    That matters more than it looks: sys.stdin is buffered, so reading one
    character of an arrow key pulls the rest of the sequence into Python's
    buffer, where select() -- which watches the descriptor -- cannot see it.
    The sequence then looks like a lone Escape followed by nothing, and every
    arrow key read as Escape.

    The terminal settings are saved on the way in and restored on the way
    out, including on the way out through an exception: a tool that leaves a
    terminal in raw mode is a tool people stop opening.
    """

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.saved = None
        self.pending = ""

    def __enter__(self):
        self.resume()
        return self

    def __exit__(self, *exc):
        self.pause()

    def resume(self):
        if self.saved is None and sys.stdin.isatty():
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)

    def pause(self):
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
            self.saved = None

    def keys(self, timeout=0.1):
        """Whatever was typed since last asked, as a list of key names."""
        if self.saved is None:
            return []
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            # Nothing followed it, so it was a bare Escape and not an arrow key.
            if self.pending == "\x1b":
                self.pending = ""
                return ["escape"]
            return []
        try:
            data = os.read(self.fd, 1024).decode("utf-8", "replace")
        except (OSError, ValueError):
            return []
        return self._parse(data)

    def _parse(self, data):
        """A run of bytes from the terminal, as key names."""
        data, self.pending = self.pending + data, ""
        found, index = [], 0
        while index < len(data):
            character = data[index]
            if character == "\x03":
                raise KeyboardInterrupt
            if character != "\x1b":
                found.append(_ordinary(character))
                index += 1
                continue

            rest = data[index + 1:]
            if not rest:
                # Escape, or the beginning of a sequence still arriving.
                self.pending = character
                break
            if rest[0] in "[O":
                match = _SEQUENCE.match(rest)
                if not match:
                    self.pending = data[index:]     # incomplete; wait for more
                    break
                name = SEQUENCES.get(match.group(0), "")
                if name:
                    found.append(name)
                index += 1 + match.end()
                continue
            found.append("escape")
            index += 1
        return [key for key in found if key]


def _ordinary(character):
    if character in ("\r", "\n"):
        return "enter"
    if character in ("\x7f", "\b"):
        return "backspace"
    if character == "\t":
        return "tab"
    return character


