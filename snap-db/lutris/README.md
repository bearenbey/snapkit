# lutris

video game preservation platform

Packaged from [lutris/lutris](https://github.com/lutris/lutris) by snapkit, from the release asset
`lutris_0.5.22_all.deb`. This snap is not published or endorsed by the upstream
project.

## Building

    cd /home/bearen/Development/snap/lutris-snap
    snapcraft

## Installing what you built

    sudo snap install --dangerous lutris_0.5.22_amd64.snap

## Updating

`snapkit` checks lutris/lutris for a newer release and rewrites
`snap/snapcraft.yaml` for you. Anything you change in that file is kept:
an update only moves the version, the source URL and its checksum.
