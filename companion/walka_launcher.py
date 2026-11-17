"""Entry script for the packaged Windows executable (see build_exe.py)."""

import sys

from walka.cli import main

if __name__ == "__main__":
    sys.exit(main())
