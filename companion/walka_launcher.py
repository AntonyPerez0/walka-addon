"""Entry script for the packaged program: Walka.exe, or Walka.app on a Mac (see build_exe.py)."""

import sys

from walka.cli import main

if __name__ == "__main__":
    sys.exit(main())
