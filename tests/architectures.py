"""Which architecture this is, and which of a release's files is for it."""


from snapforge import arch, classify, recipe, sources
from snapforge.net import NetworkError

from .harness import check, same, patched, env


def as_arch(name):
    """Run a block as though this machine were another architecture."""
    return env(arch.OVERRIDE, name)


@check("this machine's architecture is the one snapd would name")
def _():
    # dpkg is the authority, because snapd agrees with it; uname otherwise.
    same(arch.known(arch.detected()), True,
         f"{arch.detected()} is not in the spellings table")
    for machine, wanted in (("x86_64", "amd64"), ("aarch64", "arm64"),
                            ("armv7l", "armhf"), ("ppc64le", "ppc64el"),
                            ("riscv64", "riscv64"), ("i686", "i386")):
        same(arch.FROM_MACHINE[machine], wanted, machine)


@check("the override takes the spelling a person would actually type")
def _():
    # x86_64 is what gets typed, and made every amd64 asset foreign.
    for typed, wanted in (("x86_64", "amd64"), ("aarch64", "arm64"),
                          ("AMD64", "amd64"), (" amd64 ", "amd64"),
                          ("ppc64le", "ppc64el"), ("arm64", "arm64")):
        with as_arch(typed):
            same(arch.host(), wanted, typed)
            if wanted == "amd64":
                same(classify.rejection("app_amd64.deb"), "", typed)


@check("an override nothing recognises is refused, not taken literally")
def _():
    for typed in ("nonsense", "sparc", "x86_65"):
        with as_arch(typed):
            try:
                arch.host()
                assert False, f"{typed} should have been refused"
            except arch.UnknownArchitecture as exc:
                assert "is not an architecture this knows" in str(exc)
                # The message has to list them, or there is no way back.
                assert "amd64" in str(exc) and "riscv64" in str(exc)
    # A ValueError, which the command line already turns into a message.
    assert issubclass(arch.UnknownArchitecture, ValueError)


@check("a machine this does not know about is still allowed to be itself")
def _():
    # Only what a person typed: a real port not in the table still runs.
    real_which = arch.shutil.which
    arch.detected.cache_clear()
    try:
        with patched(arch.shutil, which=lambda name: None if name == "dpkg"
                     else real_which(name)), \
                patched(arch.platform, machine=lambda: "sparc64"):
            same(arch.detected(), "sparc64")
            same(arch.known("sparc64"), False, "it should not be in the table")
    finally:
        arch.detected.cache_clear()


@check("an asset is ours or somebody else's depending on the host")
def _():
    # Hardcoded x86_64 made every asset foreign on every other machine.
    cases = (("btop-x86_64-unknown-linux-musl.tar.gz", "amd64"),
             ("nvim-linux-arm64.tar.gz", "arm64"),
             ("app_armv7l.deb", "armhf"),
             ("app-riscv64.deb", "riscv64"),
             ("app-ppc64le.deb", "ppc64el"),
             ("app-linux-x64.zip", "amd64"),
             ("app-i686.deb", "i386"))
    for name, belongs_to in cases:
        with as_arch(belongs_to):
            same(classify.rejection(name), "", f"{name} on {belongs_to}")
        for stranger in ("amd64", "arm64", "riscv64"):
            if stranger == belongs_to:
                continue
            with as_arch(stranger):
                assert classify.rejection(name), \
                    f"{name} was kept on {stranger}"


@check("x86 is 32-bit, and x86_64 is not x86 with something after it")
def _():
    # The bug this guards: splitting on separators read x86_64 as 32-bit.
    with as_arch("amd64"):
        same(classify.rejection("app-x86_64.deb"), "")
        assert "x86" in classify.rejection("app-x86.deb")
    with as_arch("i386"):
        same(classify.rejection("app-x86.deb"), "")
        assert "x86_64" in classify.rejection("app-x86_64.deb")


@check("a release with nothing for this machine says so in its own words")
def _():
    with as_arch("arm64"):
        _points, _kind, why = classify.score("nvim-linux-arm64.tar.gz")
        assert "arm64 (arm64)" in why, why
    with as_arch("amd64"):
        _points, _kind, why = classify.score("btop-x86_64-linux.tar.gz")
        assert "amd64 (x86_64)" in why, why


@check("an asset naming no architecture is taken whatever the host is")
def _():
    # Usually the only build there is, and rejecting it packages nothing.
    for name in ("yt-dlp_linux.zip", "app.tar.gz"):
        for where in ("amd64", "arm64", "s390x"):
            with as_arch(where):
                same(classify.rejection(name), "", f"{name} on {where}")


@check("an upstream keeps {arch} so the record works on another machine")
def _():
    # snap-db carries records between machines, so no baked-in binary-amd64.
    made = sources.configure("apt", {"base": "https://x/apt",
                                     "package": "thing"})
    assert "{arch}" in made["index"], made["index"]
    for where in ("amd64", "arm64", "riscv64"):
        with as_arch(where):
            same(sources._fill(made["index"], base="https://x/apt"),
                 f"https://x/apt/dists/stable/main/binary-{where}/Packages")


@check("{arch} is a placeholder every shape can fill in")
def _():
    for kind, values in (
            ("index", {"url": "https://x/{arch}/", "pattern": "(a)",
                       "asset": "a-{version}-{arch}.tar.xz"}),
            ("redirect", {"url": "https://x/{arch}", "pattern": "(a)",
                          "asset": "a-{arch}.deb",
                          "download": "https://x/{version}/{asset}"}),
            ("tag-archive", {"repo": "a/b", "asset": "a-{arch}.tar.gz",
                             "download": "https://x/{tag}/{arch}"})):
        made = sources.configure(kind, values)
        same(made["asset"], values["asset"], kind)
    # And still refused where the shape genuinely cannot fill one in.
    try:
        sources.configure("index", {"url": "u", "pattern": "(a)",
                                    "asset": "a-{tag}"})
        assert False, "{tag} should still be refused for index"
    except sources.BadUpstream:
        pass


@check("a written recipe names the architecture it was written on")
def _():
    with as_arch("arm64"):
        text = recipe.build(name="demo", version="1.0", summary="a demo",
                            description="body", license_id="MIT",
                            kind="archive", url="https://x/a.tar.gz",
                            command="bin/demo")
        assert "\nplatforms:\n  arm64:\n" in text, text
    with as_arch("ppc64el"):
        text = recipe.build(name="demo", version="1.0", summary="a demo",
                            description="body", license_id="MIT",
                            kind="archive", url="https://x/a.tar.gz",
                            command="bin/demo")
        assert "\nplatforms:\n  ppc64el:\n" in text, text


@check("an apt index is read for this machine, and for `all`")
def _():
    from snapforge import versions
    index = ("Package: thing\nVersion: 2.0\nArchitecture: amd64\n"
             "Filename: pool/thing_2.0_amd64.deb\nSHA256: aa\n\n"
             "Package: thing\nVersion: 3.0\nArchitecture: arm64\n"
             "Filename: pool/thing_3.0_arm64.deb\nSHA256: bb\n\n"
             "Package: docs\nVersion: 9.0\nArchitecture: all\n"
             "Filename: pool/docs_9.0_all.deb\nSHA256: cc\n")
    with patched(versions, get_text=lambda url, timeout=30: index):
        same(versions.apt_stanza("u", "thing", want_arch="amd64")[0], "2.0")
        same(versions.apt_stanza("u", "thing", want_arch="arm64")[0], "3.0")
        # Architecture: all is installable anywhere, so it is not skipped.
        same(versions.apt_stanza("u", "docs", want_arch="s390x")[0], "9.0")
        try:
            versions.apt_stanza("u", "thing", want_arch="s390x")
            assert False, "it should have said there is nothing"
        except NetworkError as exc:
            assert "s390x" in str(exc), str(exc)
