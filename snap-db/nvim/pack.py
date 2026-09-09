"""Rebuild the Neovim snap from the official upstream release tarball."""


def build(project):
    tarball = project.artifact("nvim-linux-x86_64.tar.gz")
    project.say(f"building Neovim {project.version}  (from {tarball.name})")

    built = project.pack()
    with project.unpacked(built) as root:
        nvim = root / "usr" / "bin" / "nvim"
        if not nvim.is_file():
            project.die("no usr/bin/nvim in the packed snap: "
                        "the tarball layout changed")

        # Or the snap advertises a version its payload does not have.
        reported = project.capture(nvim, "--version").split("\n")[0].split()[1]
        project.check_version(reported.lstrip("v"), "the packed binary")

        # Classic: these come from the host, and a gap only shows up at runtime.
        project.say("checking host libraries")
        project.warn_missing(nvim)

        # Parsers are dlopened, so a gap shows only when that filetype is opened.
        for parser in sorted((root / "usr/lib/nvim/parser").glob("*.so")):
            missing = project.missing_libraries(parser)
            if missing:
                project.warn(f"{parser.name} is missing: {' '.join(missing)}")

    return project.finish(built)
