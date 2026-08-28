# sublime-text

Sophisticated text editor for code, markup and prose

Packaged by snapkit from the release asset `sublime-text_build-4200_amd64.deb`,
tracked against `apt: sublime-text`.
This snap is not published or endorsed by the upstream project.

## Building

    cd /home/bearen/Development/snap/sublimetext-snap
    snapcraft

## Installing what you built

    sudo snap install --dangerous sublime-text_4200_amd64.snap

## Updating

`snapkit` checks that upstream for a newer release and rewrites
`snap/snapcraft.yaml` for you. Anything you change in that file is kept:
an update only moves the version, the source URL and its checksum.
