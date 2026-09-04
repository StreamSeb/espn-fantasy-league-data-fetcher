"""Errors that carry a message the user can act on.

Anything raised as an FflError is printed by the CLI as a plain message with a
non-zero exit code, never as a traceback: these are expected conditions
(missing cookies, unreachable database, a season ESPN will not serve), not
bugs.
"""

from __future__ import annotations


class FflError(Exception):
    """Base class for expected, user-facing failures."""


class ConfigError(FflError):
    """A setting is missing or malformed."""


class StoreError(FflError):
    """The raw store or an output target could not be reached or written."""


class FetchError(FflError):
    """A request to ESPN failed in a way that is not worth retrying."""


class MissingDependency(FflError):
    """An optional output format needs a package that is not installed."""

    def __init__(self, fmt: str, package: str, extra: str) -> None:
        super().__init__(
            f"the '{fmt}' output format needs the {package!r} package.\n"
            f"Install it with:  pip install -e '.[{extra}]'   "
            f"(or: pip install {package})")
