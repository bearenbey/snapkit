# shotcut

cross-platform (Qt), open-source (GPLv3) video editor - mltframework/shotcut

Packaged from [mltframework/shotcut](https://github.com/mltframework/shotcut) by snapkit, from the release asset
`shotcut-linux-x86_64-26.8.1.txz`. This snap is not published or endorsed by the upstream
project.

## Building

    cd /home/bearen/Development/snap/shotcut-snap
    snapcraft

## Installing what you built

    sudo snap install --dangerous shotcut_26.8.1_amd64.snap

## Updating

`snapkit` checks mltframework/shotcut for a newer release and rewrites
`snap/snapcraft.yaml` for you. Anything you change in that file is kept:
an update only moves the version, the source URL and its checksum.
