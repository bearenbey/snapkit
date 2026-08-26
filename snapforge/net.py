"""HTTP, downloads and checksums.

Everything here is one small request against a page that is not rate limited,
or one large download. Nothing talks to api.github.com: a shared address gets
403s from it often enough that a tool which fails on the first thing you ask
of it is not worth having.
"""

import hashlib
import time
import urllib.error
import urllib.request

META_TIMEOUT = 30       # seconds for one metadata request
DOWNLOAD_TIMEOUT = 60   # seconds of no progress at all on a download
CHUNK = 1 << 18

# urllib says it is Python, and some CDNs treat that differently.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) snapkit/1"


class NetworkError(Exception):
    """Upstream could not be reached, or did not say what was expected."""


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
            return opener.open(request, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            if attempt < retries:
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
    """The sha256 of a file already on disk, read in blocks.

    A vendored library is checked against the checksum written down beside it
    rather than fetched again, so this is what makes a second build of the
    same project need no network at all.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url, dest, sha="", on_progress=None):
    """Fetch a URL to `dest`, verifying `sha` when one is known.

    Returns the sha256 of what arrived. The download lands on `dest.part` and
    is moved into place only once it is complete and verified, so an
    interrupted run never leaves a half file that a later build would open
    and trust.
    """
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
