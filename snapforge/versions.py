"""Version ordering, apt Packages indexes, and reading a packaged version."""

import re

from .net import NetworkError, get_text


def version_key(text):
    """Order versions the way `sort -V` does."""
    parts = []
    for token in re.findall(r"\d+|\D+", text):
        if token.isdigit():
            parts.append((1, int(token), ""))
        else:
            parts.append((0, 0, token))
    return parts


def newest(versions):
    return max(versions, key=version_key) if versions else ""


# --- Debian version ordering: dpkg's verrevcmp(), without a fork each ------


def _is_digit(char):
    return "0" <= char <= "9"


def _is_alpha(char):
    return "a" <= char <= "z" or "A" <= char <= "Z"


def _order(char):
    """One character's place in Debian's alphabet."""
    if _is_digit(char):
        return 0
    if _is_alpha(char):
        return ord(char)
    if char == "~":
        return -1
    if char:
        return ord(char) + 256
    return 0


def _verrevcmp(a, b):
    """Compare one part of a version -- an upstream version or a revision."""
    i = j = 0
    while i < len(a) or j < len(b):
        first_diff = 0

        # the non-numeric stretch, a character at a time
        while ((i < len(a) and not _is_digit(a[i]))
               or (j < len(b) and not _is_digit(b[j]))):
            left = _order(a[i] if i < len(a) else "")
            right = _order(b[j] if j < len(b) else "")
            if left != right:
                return left - right
            i += 1
            j += 1

        # The numeric stretch as a number: leading zeroes carry no weight.
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        while (i < len(a) and j < len(b)
               and _is_digit(a[i]) and _is_digit(b[j])):
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < len(a) and _is_digit(a[i]):
            return 1
        if j < len(b) and _is_digit(b[j]):
            return -1
        if first_diff:
            return first_diff
    return 0


def deb_split(version):
    """A Debian version as (epoch, upstream version, revision)."""
    epoch, colon, rest = version.partition(":")
    if not colon or not epoch.isdigit():
        epoch, rest = "0", version
    upstream, hyphen, revision = rest.rpartition("-")
    if not hyphen:
        upstream, revision = rest, ""
    return int(epoch), upstream, revision


def deb_compare(a, b):
    """`dpkg --compare-versions a <op> b`, without the fork."""
    # Empty sorts first as a rule, not by character: `~` sorts before the end.
    blank_a, blank_b = a in ("", "<unknown>"), b in ("", "<unknown>")
    if blank_a or blank_b:
        return 0 if blank_a and blank_b else (-1 if blank_a else 1)

    epoch_a, upstream_a, revision_a = deb_split(a)
    epoch_b, upstream_b, revision_b = deb_split(b)
    if epoch_a != epoch_b:
        return -1 if epoch_a < epoch_b else 1
    for left, right in ((upstream_a, upstream_b), (revision_a, revision_b)):
        answer = _verrevcmp(left, right)
        if answer:
            return answer
    return 0


def deb_key(version):
    """`deb_compare` as a sort key, for max() and sorted()."""
    return _DebVersion(version)


class _DebVersion:
    __slots__ = ("version",)

    def __init__(self, version):
        self.version = version

    def __lt__(self, other):
        return deb_compare(self.version, other.version) < 0

    def __eq__(self, other):
        return deb_compare(self.version, other.version) == 0


def yaml_version(path):
    """The `version:` field of a snapcraft.yaml or meta/snap.yaml."""
    for line in _lines(path):
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return ""


def _lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().splitlines()
    except FileNotFoundError:
        return []


def apt_stanza(index_url, package, want=""):
    """One package out of an apt Packages index."""
    rows = []
    fields = {}

    def flush():
        if (fields.get("Package") == package
                and fields.get("Architecture") == "amd64"
                and all(fields.get(k) for k in ("Version", "Filename", "SHA256"))):
            rows.append((fields["Version"], fields["Filename"], fields["SHA256"]))
        fields.clear()

    for line in get_text(index_url).splitlines():
        if not line.strip():
            flush()
            continue
        if ": " in line and not line.startswith(" "):
            key, value = line.split(": ", 1)
            fields[key] = value.strip()
    flush()

    if not rows:
        raise NetworkError(f"no {package} stanza in {index_url}")
    if want:
        for row in rows:
            if row[0] == want:
                return row
        raise NetworkError(f"{package} {want} is not in {index_url}")
    # Debian's ordering: under sort -V a 3.10.0~beta.2 reads as the newer one.
    return max(rows, key=lambda row: deb_key(row[0]))
