#!/usr/bin/env python3
"""Register the snap projects that live beside this one."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from snapforge import adopt                                   # noqa: E402
from snapforge.db import Database                             # noqa: E402
from snapforge.report import PlainReporter                    # noqa: E402

# --- how an update reaches each project -------------------------------------

GITHUB = {
    "btop": "aristocratos/btop",
    "defold": "defold/defold",
    "floorp": "Floorp-Projects/Floorp",
    "freetube": "FreeTubeApp/FreeTube",
    "godot": "godotengine/godot",
    "helium": "imputnet/helium-linux",
    "irssi": "irssi/irssi",
    "neovim": "neovim/neovim",
    "ollama": "ollama/ollama",
    "spotube": "KRTirtho/spotube",
    "transmission": "transmission/transmission",
    # The portable tarballs come from a repository of their own.
    "ungoogled-chromium": "ungoogled-software/ungoogled-chromium-portablelinux",
    "yt-dlp": "yt-dlp/yt-dlp",
    "zen": "zen-browser/desktop",
}

SIGNAL_APT = "https://updates.signal.org/desktop/apt"
SUBLIME_APT = "https://download.sublimetext.com"
UNITY_APT = "https://hub.unity3d.com/linux/repos/deb"

CONFIG = {
    "btop": dict(
        style="artifact", asset_glob="btop-x86_64-unknown-linux-musl.*",
        pack="pack.py"),

    "defold": dict(
        style="artifact", asset_glob="Defold-x86_64-linux.zip", pack="pack.py"),

    # No index: the download redirects and the version is in the path.
    "discord": dict(
        style="artifact", asset_glob="discord-*.deb",
        upstream=dict(kind="redirect", asset="discord-{version}.deb",
                      url="https://discord.com/api/download"
                          "?platform=linux&format=deb",
                      pattern=r"/apps/linux/([^/]+)/",
                      download="https://dl.discordapp.net/apps/linux/"
                               "{version}/{asset}")),

    # One index for every release; only the top-level tarballs match.
    # A mirror, because ftp.gnu.org refuses as often as it answers.
    "emacs": dict(
        style="artifact", asset_glob="emacs-*.tar.xz",
        upstream=dict(kind="index", url="https://mirrors.kernel.org/gnu/emacs/",
                      pattern=r'emacs-(\d+\.\d+(?:\.\d+)?)\.tar\.xz"',
                      asset="emacs-{version}.tar.xz"),
        verify=dict(kind="gpg", suffix=".sig")),

    # Release candidates are ffmpeg-N.N-rcN and do not match this pattern.
    "ffmpeg": dict(
        style="recipe",
        upstream=dict(kind="index", url="https://ffmpeg.org/releases/",
                      pattern=r"ffmpeg-(\d+\.\d+(?:\.\d+)?)\.tar\.xz",
                      asset="ffmpeg-{version}.tar.xz"),
        source_anchor=r"^(\s*source:\s*).*/ffmpeg-.*\.tar\.xz\s*$",
        verify=dict(kind="gpg", suffix=".asc")),

    # The name carries no version, so it is overwritten in place each release.
    "floorp": dict(
        style="artifact", asset_glob="floorp-linux-x86_64.tar.xz",
        checksums=dict(url="{base}/hashes.txt", required=False),
        pack="pack.py"),

    "freetube": dict(
        style="artifact", asset_glob="freetube_*_amd64.deb", pack="pack.py"),

    "godot": dict(
        style="artifact", asset_glob="Godot_v*_linux.x86_64.zip", pack="pack.py"),

    # The .deb carries a Debian revision the bare tag does not.
    "helium": dict(
        style="artifact", asset_glob="helium-bin_*_amd64.deb", pack="pack.py"),

    "irssi": dict(
        style="recipe",
        source_anchor=r"^(\s*source:\s*).*/irssi-.*\.tar\.xz\s*$"),

    # No source tarball: the release is the tag and GitHub rolls the archive.
    "mpv": dict(
        style="recipe",
        upstream=dict(kind="tag-archive", repo="mpv-player/mpv", prefix="v",
                      asset="mpv-{version}.tar.gz",
                      download="https://github.com/mpv-player/mpv/archive/"
                               "refs/tags/{tag}.tar.gz"),
        source_anchor=r"^(\s*source:\s*).*/archive/refs/tags/v.*\.tar\.gz\s*$",
        verify=dict(kind="tar-member", member="mpv-{version}/MPV_VERSION")),

    "neovim": dict(
        style="artifact", asset_glob="nvim-linux-x86_64.tar.gz", pack="pack.py"),

    "ollama": dict(
        style="recipe",
        checksums=dict(url="{base}/sha256sum.txt"),
        source_anchor=r"^(\s*source:\s*).*/ollama-linux-amd64\.tar\.zst\s*$"),

    # Like mpv; the unversioned data repos follow their default branches.
    "retroarch": dict(
        style="recipe",
        upstream=dict(kind="tag-archive", repo="libretro/RetroArch", prefix="v",
                      asset="retroarch-{version}.tar.gz",
                      download="https://github.com/libretro/RetroArch/archive/"
                               "refs/tags/{tag}.tar.gz"),
        source_anchor=r"^(\s*source:\s*).*/RetroArch/archive/refs/tags/"
                      r"v.*\.tar\.gz\s*$",
        verify=dict(kind="tar-member",
                    member="RetroArch-{version}/version.all")),

    # Only the apt repository publishes the .deb, and it carries the SHA256.
    "signal": dict(
        style="recipe", write_version=True,
        upstream=dict(kind="apt", base=SIGNAL_APT, package="signal-desktop",
                      index=f"{SIGNAL_APT}/dists/xenial/main/binary-{{arch}}/"
                            f"Packages"),
        source_anchor=r"^(\s*source:\s*).*/signal-desktop_.*\.deb\s*$"),

    # The .deb asset name carries no version, so it is overwritten in place.
    "spotube": dict(
        style="artifact", asset_glob="Spotube-linux-x86_64.deb",
        checksums=dict(url="{base}/RELEASE.sha256sum", required=False)),

    "sublimetext": dict(
        style="artifact", asset_glob="sublime-text_build-*_amd64.deb",
        upstream=dict(kind="apt", base=SUBLIME_APT, package="sublime-text",
                      index=f"{SUBLIME_APT}/apt/stable/Packages")),

    "transmission": dict(
        style="artifact", asset_glob="transmission-*.tar.xz", pack="pack.py"),

    # The tag carries a packaging revision, so the version is the tag as-is.
    "ungoogled-chromium": dict(
        style="artifact", pack="pack.py",
        asset_glob="ungoogled-chromium-*-x86_64_linux.tar.xz"),

    # Upstream's name changes per release, so rename it on the way in.
    "unityhub": dict(
        style="artifact", local_asset="UnityHubSetup-amd64.deb",
        asset_glob="UnityHubSetup-amd64.deb", pack="pack.py",
        upstream=dict(kind="apt", base=UNITY_APT, package="unityhub",
                      index=f"{UNITY_APT}/dists/stable/main/binary-{{arch}}/"
                            f"Packages")),

    # Upstream publishes SHA2-256SUMS beside every asset of a release.
    "yt-dlp": dict(
        style="recipe",
        checksums=dict(url="{base}/SHA2-256SUMS"),
        source_anchor=r"^(\s*source:\s*).*/yt-dlp_linux\.zip\s*$"),

    # The tag is the version, "b" and all; the tarball name carries none.
    "zen": dict(
        style="artifact", asset_glob="zen.linux-x86_64.tar.xz", pack="pack.py"),
}


def projects(wanted=()):
    """Every sibling snap project, this one excepted."""
    found = sorted(p for p in HERE.parent.glob("*-snap")
                   if p.is_dir() and p != HERE)
    if wanted:
        names = {w.removesuffix("-snap") for w in wanted}
        found = [p for p in found if p.name.removesuffix("-snap") in names]
    return found


def describe(name, config, repo):
    if repo:
        return f"{repo}  {config.get('style', 'recipe')}"
    if config.get("upstream"):
        return (f"{config['upstream']['kind']}  "
                f"{config.get('style', 'recipe')}")
    return "upstream unknown, will be left unchecked"


def main(argv):
    dry = "--dry-run" in argv
    wanted = [a for a in argv if not a.startswith("-")]
    found = projects(wanted)
    if not found:
        print(f"no matching *-snap directories beside {HERE}", file=sys.stderr)
        return 1

    db = Database()
    reporter = PlainReporter()
    width = max(len(d.name) for d in found)

    for directory in found:
        name = directory.name.removesuffix("-snap")
        config = CONFIG.get(name, {})
        repo = GITHUB.get(name)

        if dry:
            print(f"  {directory.name:<{width}}  {describe(name, config, repo)}")
            continue

        try:
            snap, _recipe, _is_snapcraft, _confirmed = adopt.read(directory,
                                                                 repo=repo)
        except adopt.NotAProject as exc:
            reporter.warn(f"{directory.name}: {exc}")
            continue

        # `pack` replaced build_with, so clear it rather than let it win.
        for key, value in config.items():
            setattr(snap, key, value)
        if snap.pack:
            snap.build_with = ""

        existing = db.get(name) if name in db else None
        if existing:
            # Keep what the register learnt by building: how often, and when.
            snap.builds, snap.history = existing.builds, existing.history
            snap.created = existing.created
        db.add(snap, replace=True)
        print(f"  {directory.name:<{width}}  {describe(name, config, repo)}")

    if not dry:
        print()
        print(f"{len(found)} registered in {db.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
