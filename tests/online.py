"""The checks that talk to GitHub, run only when asked to with --online."""

from snapforge import classify, github

from .harness import check, same, raises


@check("a release resolves, and something in it can be packaged")
def _():
    for repo, kind in (("aristocratos/btop", "archive"),
                       ("sharkdp/bat", "deb"),
                       ("neovim/neovim", "archive")):
        release = github.release(repo)
        assert release.version, repo
        best = classify.classify(release.assets)
        assert best, f"{repo}: nothing packageable"
        same(best[0].kind, kind, repo)


@check("a licence is read off the repository's own licence file")
def _():
    for repo, want in (("aristocratos/btop", "Apache-2.0"),
                       ("neovim/neovim", "Apache-2.0"),
                       ("FreeTubeApp/FreeTube", "AGPL-3.0")):
        same(github.licence_of(repo), want, repo)


@check("a repository that does not exist is an error, not a crash")
def _():
    with raises((github.NotFound, github.NetworkError), "should have raised"):
        github.release("this-owner-does-not/exist-at-all-xyzzy")
