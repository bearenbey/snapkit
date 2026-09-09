"""Turn a GitHub repository into a snap package, and keep it that way."""

# Spelled here, in pyproject.toml and in snap/snapcraft.yaml; a test holds
# the three together.
__version__ = "0.3.1"

__all__ = [
    "adopt", "arch", "build", "classify", "cli", "db", "depends", "elf",
    "github", "inspect", "keys", "local", "net", "platform", "project",
    "recipe", "report", "rewrite", "screen", "snapdb", "sources", "tui",
    "update", "versions"
]
