# clamui

Modern graphical interface for ClamAV antivirus scanner

Packaged by snapkit from the release asset `clamui_0.4.0_all.deb`,
tracked against [linx-systems/clamui](https://github.com/linx-systems/clamui).
This snap is not published or endorsed by the upstream project.

## Building

    cd /home/bearen/Development/snap/clamui-snap
    snapcraft

## Installing what you built

    sudo snap install --dangerous clamui_0.4.0_amd64.snap

## Updating

`snapkit` checks that upstream for a newer release and rewrites
`snap/snapcraft.yaml` for you. Anything you change in that file is kept:
an update only moves the version, the source URL and its checksum.
