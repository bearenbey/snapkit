# discord

Chat for Communities and Friends

Packaged by snapkit from the release asset `discord-1.0.156.deb`,
tracked against `redirect: https://discord.com/api/download?platform=linux&format=deb`.
This snap is not published or endorsed by the upstream project.

## Building

    cd /home/bearen/Development/snap/discord-snap
    snapcraft

## Installing what you built

    sudo snap install --dangerous discord_1.0.156_amd64.snap

## Updating

`snapkit` checks that upstream for a newer release and rewrites
`snap/snapcraft.yaml` for you. Anything you change in that file is kept:
an update only moves the version and the file the recipe names.
