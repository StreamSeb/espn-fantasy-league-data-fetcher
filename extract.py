#!/usr/bin/env python3
"""Backwards-compatible entry point.

The tool lives in the ffl_history package now; this keeps `python extract.py`
working. `ffl-history` (installed by pip) and `python -m ffl_history` are
equivalent.
"""

import sys

from ffl_history.cli import main

if __name__ == "__main__":
    # With no arguments, do the obvious thing rather than printing help.
    sys.exit(main(sys.argv[1:] or ["run"]))
