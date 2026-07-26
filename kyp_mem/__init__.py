"""KYP-MEM — Know Your Project Memory. Headless knowledge base for AI agents."""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    # Single source of truth: the installed package metadata, which comes from
    # pyproject.toml. Hardcoding it here let __version__ drift to 0.4.2 while
    # the package published as 0.9.0.
    __version__ = _version("kyp-mem")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0.dev0"
