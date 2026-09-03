"""What an ELF binary says it needs, read without running or resolving it."""

import struct
from pathlib import Path

MAGIC = b"\x7fELF"

PT_LOAD = 1
PT_DYNAMIC = 2
DT_NULL = 0
DT_NEEDED = 1
DT_STRTAB = 5
DT_SONAME = 14
DT_RUNPATH = 29
DT_RPATH = 15


class NotAnELF(Exception):
    """The file is not an ELF object this can read."""


class _Reader:
    """One ELF file's headers, in whatever width and order it was built."""

    def __init__(self, blob):
        if blob[:4] != MAGIC:
            raise NotAnELF("not an ELF file")
        self.blob = blob
        self.wide = blob[4] == 2
        self.little = blob[5] == 1
        if blob[4] not in (1, 2) or blob[5] not in (1, 2):
            raise NotAnELF("ELF header says neither 32/64-bit nor an endianness")

    def at(self, form, offset):
        """One struct field, in this file's byte order."""
        prefix = "<" if self.little else ">"
        size = struct.calcsize(prefix + form)
        if offset < 0 or offset + size > len(self.blob):
            raise NotAnELF("a header points past the end of the file")
        return struct.unpack_from(prefix + form, self.blob, offset)[0]

    def word(self, offset):
        """An address or offset, which is where the two widths differ."""
        return self.at("Q" if self.wide else "I", offset)

    def program_headers(self):
        """(type, file offset, virtual address, file size) for each segment."""
        table = self.word(0x20 if self.wide else 0x1C)
        size = self.at("H", 0x36 if self.wide else 0x2A)
        count = self.at("H", 0x38 if self.wide else 0x2C)
        for index in range(count):
            base = table + index * size
            kind = self.at("I", base)
            if self.wide:
                yield kind, self.word(base + 8), self.word(base + 16), \
                    self.word(base + 32)
            else:
                yield kind, self.word(base + 4), self.word(base + 8), \
                    self.word(base + 16)

    def offset_of(self, address, loads):
        """Where a virtual address lands in the file, or None if nowhere."""
        for _kind, offset, vaddr, size in loads:
            if vaddr <= address < vaddr + size:
                return offset + (address - vaddr)
        return None

    def string(self, base, index):
        """One NUL-terminated string out of the dynamic string table."""
        start = base + index
        end = self.blob.find(b"\0", start)
        if start >= len(self.blob) or end < 0:
            return ""
        return self.blob[start:end].decode("utf-8", "replace")


def _dynamic(reader):
    """The dynamic entries as (tag, value), and where the strings live."""
    headers = list(reader.program_headers())
    loads = [h for h in headers if h[0] == PT_LOAD]
    dynamic = next((h for h in headers if h[0] == PT_DYNAMIC), None)
    if dynamic is None:
        return [], None
    _kind, offset, _vaddr, size = dynamic
    step = 16 if reader.wide else 8
    entries, strtab = [], None
    for index in range(size // step):
        base = offset + index * step
        tag = reader.word(base)
        value = reader.word(base + step // 2)
        if tag == DT_NULL:
            break
        if tag == DT_STRTAB:
            strtab = reader.offset_of(value, loads)
        entries.append((tag, value))
    return entries, strtab


def read(path):
    """What this binary needs, what it calls itself, and where it looks."""
    try:
        blob = Path(path).read_bytes()
    except OSError as exc:
        raise NotAnELF(str(exc)) from exc
    reader = _Reader(blob)
    entries, strtab = _dynamic(reader)
    if strtab is None:
        return [], "", []
    needed, soname, paths = [], "", []
    for tag, value in entries:
        if tag == DT_NEEDED:
            needed.append(reader.string(strtab, value))
        elif tag == DT_SONAME:
            soname = reader.string(strtab, value)
        elif tag in (DT_RUNPATH, DT_RPATH):
            paths += reader.string(strtab, value).split(":")
    return [n for n in needed if n], soname, [p for p in paths if p]


def needed(path):
    """The sonames this binary loads at startup, in the order it names them."""
    return read(path)[0]


def soname_of(path):
    """What a shared library calls itself, which is how others ask for it."""
    return read(path)[1]


def is_elf(path):
    """Whether this file is an ELF object at all, without reading the rest."""
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == MAGIC
    except OSError:
        return False
