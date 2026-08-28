"""HTTP, downloads and checksums."""

import hashlib
import platform
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

META_TIMEOUT = 30       # seconds for one metadata request
DOWNLOAD_TIMEOUT = 60   # seconds of no progress at all on a download
CHECK_TIMEOUT = 15      # seconds for a whole check, however many requests
CHUNK = 1 << 18

# urllib says it is Python, and some CDNs treat that differently. The machine
# is the uname spelling, because that is what a browser puts there, and an
# endpoint that picks a build off the user agent should pick this one.
USER_AGENT = f"Mozilla/5.0 (X11; Linux {platform.machine()}) snapkit/1"


class NetworkError(Exception):
    """Upstream could not be reached, or did not say what was expected."""


# Per thread, because a check of the whole register runs several at once.
_clock = threading.local()


@contextmanager
def deadline(seconds):
    """Bound everything done in here, however many requests it turns into.

    A per-request timeout does not answer "how long may this take", since
    one check can resolve an index, then a release, then an asset. This
    does, and the requests inside it shorten to whatever is left.
    """
    was = getattr(_clock, "until", None)
    _clock.until = time.monotonic() + seconds
    try:
        yield
    finally:
        _clock.until = was


def _left(timeout, url):
    """What one request may take, against whatever deadline is running."""
    until = getattr(_clock, "until", None)
    if until is None:
        return timeout
    remaining = until - time.monotonic()
    if remaining <= 0:
        raise NetworkError(f"{url}: timed out")
    return min(timeout, remaining)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn a redirect into an HTTPError instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_follow = urllib.request.build_opener()
_stay = urllib.request.build_opener(_NoRedirect)


def _open(opener, url, method="GET", timeout=META_TIMEOUT, retries=1):
    """Open a URL, retrying on a transient failure."""
    last = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(
                url, method=method, headers={"User-Agent": USER_AGENT})
            return opener.open(request, timeout=_left(timeout, url))
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            # A retry it has no time for is a wait nobody asked for.
            if attempt < retries:
                _left(1 + attempt, url)
                time.sleep(1 + attempt)
    raise NetworkError(f"{url}: {last}")


def get_text(url, timeout=META_TIMEOUT):
    """The body of a GET, following redirects, decoded as text."""
    try:
        with _open(_follow, url, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise NetworkError(f"{url}: HTTP {exc.code}") from exc


def head_location(url, timeout=META_TIMEOUT):
    """Where a URL redirects to, without following it. "" if it does not."""
    try:
        with _open(_stay, url, method="HEAD", timeout=timeout) as response:
            return response.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            return exc.headers.get("Location", "")
        raise NetworkError(f"{url}: HTTP {exc.code}") from exc


def sha256_file(path):
    """The sha256 of a file already on disk, read in blocks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url, dest, sha="", on_progress=None):
    """Fetch a URL to `dest`, verifying `sha` when one is known."""
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    done = 0

    try:
        with _open(_follow, url, timeout=DOWNLOAD_TIMEOUT, retries=2) as response:
            total = int(response.headers.get("Content-Length") or 0)
            if on_progress:
                on_progress(0, total)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(part, "wb") as out:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    out.write(block)
                    digest.update(block)
                    done += len(block)
                    if on_progress:
                        on_progress(done, total)
    except urllib.error.HTTPError as exc:
        part.unlink(missing_ok=True)
        raise NetworkError(f"{url}: HTTP {exc.code}") from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise

    got = digest.hexdigest()
    if sha and got != sha:
        part.unlink(missing_ok=True)
        raise NetworkError(f"{dest.name}: sha256 does not match what upstream published")
    part.replace(dest)
    return got
