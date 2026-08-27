# clamui

Modern graphical interface for ClamAV antivirus scanner

Packaged from [linx-systems/clamui](https://github.com/linx-systems/clamui) by snapkit, from the release asset
`clamui_0.4.0_all.deb`. This snap is not published or endorsed by the upstream
project.

## Building

    cd /home/bearen/Development/snap/clamui-snap
    snapcraft

## Installing what you built

    sudo snap install --dangerous clamui_0.4.0_amd64.snap

## Updating

`snapkit` checks linx-systems/clamui for a newer release and rewrites
`snap/snapcraft.yaml` for you. Anything you change in that file is kept:
an update only moves the version, the source URL and its checksum.
