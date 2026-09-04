#!/usr/bin/env python3
"""Backwards-compatible entry point.

The tool lives in the espn_fantasy package now; this keeps `python extract.py`
working. `espn-fantasy` (installed by pip) and `python -m espn_fantasy` are
equivalent.
"""

import sys

from espn_fantasy.cli import main

if __name__ == "__main__":
    # With no arguments, do the obvious thing rather than printing help.
    sys.exit(main(sys.argv[1:] or ["run"]))
