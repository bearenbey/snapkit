"""Writing the snapcraft.yaml."""

import json
import re
import textwrap

from . import arch, classify
from .rewrite import repoint_lines

BASE = "core24"

# Kept short: an interface is easier to add later than to justify now.
PLUGS = {
    "cli": ["home", "network", "removable-media"],
    "gui": ["home", "network", "audio-playback", "opengl", "removable-media"],
    "electron": ["browser-support", "audio-record", "camera",
                 "password-manager-service"],
}

MAX_NAME = 40


_YAML_WORDS = frozenset(("true", "false", "yes", "no", "on", "off", "null", "~"))


def snap_name(text):
    """A snap name out of a repository name."""
    name = (text or "").strip().lower()
    name = re.sub(r"[._\s]+", "-", name)
    name = re.sub(r"[^a-z0-9-]", "", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    # -linux and -bin describe the download; "app" is usually part of the name.
    name = re.sub(r"-(linux|desktop|bin|releases?)$", "", name)
    name = name.strip("-")[:MAX_NAME].strip("-")
    if not name:
        raise ValueError(f"no usable snap name in {text!r}")
    if name[0].isdigit():
        # A name must start with a letter, and this is cheaper than a build.
        name = ("s-" + name)[:MAX_NAME].strip("-")
    return name


def _scalar(text):
    """A one-line value, quoted only when YAML would read it as something else."""
    plain = re.fullmatch(r"[A-Za-z0-9_(][^#:\"\\\[\]{}&*!|>%@`]*", text)
    if plain and not text.endswith(" ") and text.lower() not in _YAML_WORDS:
        return text
    return json.dumps(text, ensure_ascii=False)


def summarise(text, fallback):
    """A summary: one line, no more than 78 characters, always something."""
    line = " ".join((text or "").split()) or fallback
    line = line.split(". ")[0].strip().rstrip(".")
    return line[:78] or fallback


def describe(text, summary, repo_url):
    """The long description, always ending with where it came from."""
    body = " ".join((text or summary or "").split())
    paragraphs = textwrap.fill(body, width=76) if body else summary
    return f"{paragraphs}\n\nPackaged from the upstream release at {repo_url}.\n" \
           f"This snap is not published or endorsed by the upstream project."


def _block(text, indent="  "):
    """A yaml block scalar body, indented, with blank lines kept."""
    lines = text.splitlines() or [""]
    return "\n".join(f"{indent}{line}".rstrip() for line in lines)


def plugs_for(traits):
    """The interfaces this application is going to need."""
    chosen = list(PLUGS["gui" if "gui" in traits else "cli"])
    if "electron" in traits:
        for plug in PLUGS["electron"]:
            if plug not in chosen:
                chosen.append(plug)
    return chosen


# snapcraft knows fewer extensions than the classifier accepts, so say which.
SOURCE_TYPES = ((".zip", "zip"), (".7z", "7z"))


def source_type_for(url):
    """What to tell snapcraft an archive is, rather than let it guess."""
    name = url.rsplit("/", 1)[-1].lower().split("?", 1)[0]
    for suffix, kind in SOURCE_TYPES:
        if name.endswith(suffix):
            return kind
    return "tar"


def app_id(desktop):
    """The bus name a desktop entry implies, or "" when it implies none."""
    if not desktop:
        return ""
    stem = desktop.rsplit("/", 1)[-1]
    if stem.endswith(".desktop"):
        stem = stem[:-len(".desktop")]
    # Reverse-DNS or nothing: a bare `freetube.desktop` is not a bus name.
    parts = stem.split(".")
    if len(parts) < 3 or not all(re.fullmatch(r"[A-Za-z0-9_-]+", p) for p in parts):
        return ""
    return stem


def staged(packages):
    """The stage-packages block, or nothing when the platform has it all."""
    if not packages:
        return ""
    return ("\n    # What the payload asks for that the base and the extension\n"
            "    # do not already supply. Read off the binary, not guessed at.\n"
            "    # A library it opens later by name is not visible here.\n"
            "    stage-packages:\n"
            + "".join(f"      - {one}\n" for one in packages).rstrip("\n"))


LAUNCHER = """#!/bin/bash
# chrome-sandbox must be setuid root, and no file in a snap can be. See README.
set -e

args=()

# Electron's other sandbox needs no setuid bit, so keep it where there is one.
if ! unshare --user --map-root-user true 2>/dev/null; then
    args+=(--no-sandbox)
fi

exec "$SNAP/{command}" "${{args[@]}}" "$@"
"""


def launcher_script(command):
    """The wrapper an Electron app is started through, and why it needs one."""
    return LAUNCHER.format(command=command)


def launcher_part(name):
    """The part that puts that wrapper on the path inside the snap."""
    return (f"  launcher:\n"
            f"    plugin: dump\n"
            f"    source: snap/local\n"
            f"    organize:\n"
            f"      {name}-launch: bin/{name}-launch\n")


def part_for(kind, name, url, sha="", packages=()):
    """The `parts:` stanza that gets the payload into the snap."""
    checksum = f"\n    source-checksum: sha256/{sha}" if sha else ""
    extra = staged(packages)
    if kind == classify.DEB:
        return (f"  {name}:\n"
                f"    # snapcraft unpacks the .deb and `dump` stages what was in it.\n"
                f"    plugin: dump\n"
                f"    source: {url}\n"
                f"    source-type: deb{checksum}{extra}\n")
    if kind == classify.APPIMAGE:
        return (f"  {name}:\n"
                f"    # An AppImage is an ELF launcher with a squashfs on the end, and\n"
                f"    # only the launcher knows where that starts -- so it is asked to\n"
                f"    # unpack itself, and what falls out is what gets staged.\n"
                f"    plugin: nil\n"
                f"    source: {url}\n"
                f"    source-type: file{checksum}{extra}\n"
                f"    override-build: |\n"
                f"      # Not every project spells the extension the same way --\n"
                f"      # neovim ships .appimage, most ship .AppImage -- and the\n"
                f"      # name changes with the version, so the match is on any\n"
                f"      # spelling of it, the way the classifier accepts any.\n"
                f"      image=$(ls *.[Aa][Pp][Pp][Ii][Mm][Aa][Gg][Ee] | head -n1)\n"
                f'      chmod +x "$image"\n'
                f'      "./$image" --appimage-extract > /dev/null\n'
                f'      cp -a squashfs-root/. "$CRAFT_PART_INSTALL/"\n')
    return (f"  {name}:\n"
            f"    # A plain archive: snapcraft unpacks it and folds away the single\n"
            f"    # top-level directory, which is why the paths above start below it.\n"
            f"    plugin: dump\n"
            f"    source: {url}\n"
            f"    source-type: {source_type_for(url)}{checksum}{extra}\n")


def build(*, name, version, summary, description, license_id, kind, url,
          command, desktop="", icon="", traits=(), sha="", repo_url="",
          title="", confinement="strict", grade="stable", base=BASE, plugs=None,
          packages=()):
    """The whole snapcraft.yaml, as text."""
    traits = set(traits or ())
    plugs = plugs if plugs is not None else plugs_for(traits)
    gui = "gui" in traits

    out = [f"# Written by snapkit from {repo_url or 'an upstream release'}.",
           "# Everything here is a starting point -- edit it, it is yours now.",
           "",
           f"name: {name}",
           f"base: {base}",
           f"version: '{version}'",
           f"summary: {_scalar(summary)}",
           "description: |",
           _block(description),
           f"grade: {grade}",
           f"confinement: {confinement}"]
    if license_id:
        out.append(f"license: {license_id}")
    if title:
        out.append(f"title: {_scalar(title)}")
    if repo_url:
        # A repack is exactly when somebody wants to see where it came from.
        out += [f"website: {repo_url}",
                f"source-code: {repo_url}",
                f"issues: {repo_url}/issues",
                f"contact: {repo_url}/issues"]
    # Electron cannot start from its own binary in a snap; see launcher_script.
    wrapped = "electron" in traits and command
    out += ["",
            "platforms:",
            f"  {arch.host()}:",
            "",
            "apps:",
            f"  {name}:",
            f"    command: bin/{name}-launch" if wrapped
            else f"    command: {command}"]
    if gui:
        out.append("    # The gnome extension wires up the desktop, fonts, themes and")
        out.append("    # GTK/GL stack from the platform snap rather than bundling them.")
        out.append("    extensions: [gnome]")
    if desktop:
        out.append(f"    desktop: {desktop}")
    if plugs:
        out.append("    plugs:")
        out += [f"      - {plug}" for plug in plugs]
    bus_name = app_id(desktop)
    if bus_name:
        out += ["    # A GtkApplication registers its application id on the session",
                "    # bus, and confinement refuses a name the snap has not declared.",
                "    slots:",
                f"      - dbus-{name}",
                "", "slots:", f"  dbus-{name}:", "    interface: dbus",
                "    bus: session", f"    name: {bus_name}"]

    out += ["", "parts:", part_for(kind, name, url, sha, packages).rstrip()]
    if wrapped:
        out += ["", launcher_part(name).rstrip()]
    if icon:
        out += ["",
                "# The icon snapd shows in the launcher. `icon:` is resolved against",
                "# this project directory, not against the payload, so the one found",
                "# inside the release was copied here next to the recipe.",
                f"icon: {icon}"]
    return "\n".join(out).rstrip() + "\n"


def from_record(snap, payload, url, sha="", description="", icon="",
                packages=()):
    """The recipe for a record and the payload it was made from."""
    repo_url = f"https://github.com/{snap.repo}" if snap.repo else ""
    summary = summarise(payload.summary or description,
                        f"{snap.name}, packaged as a snap")
    return build(
        name=snap.name,
        version=snap.version,
        summary=summary,
        description=describe(payload.description or description, summary, repo_url),
        license_id=snap.license,
        kind=snap.kind,
        url=url,
        command=snap.command,
        desktop=payload.desktop,
        icon=icon,
        traits=payload.traits,
        plugs=snap.plugs,
        sha=sha,
        repo_url=repo_url,
        title=snap.repo.split("/")[-1] if snap.repo else "",
        confinement=snap.confinement,
        grade=snap.grade,
        base=snap.base,
        packages=packages,
    )


def repoint(yaml_text, old_version, new_version, old_url, new_url, sha=""):
    """Move an existing recipe onto a newer release, in place."""
    out = repoint_lines(yaml_text.splitlines(), new_url, sha, new_version,
                        old_url=old_url, old_version=old_version)
    return "\n".join(out) + "\n"
