"""Is the Python packed into the program the one it was built with, and new enough?

    python tools/check_bundled_python.py "<what the program's selfcheck printed>"

Run with the Python the program was built with (the build's own). The
program's "selfcheck" names the Python and the OpenSSL it runs on. A
program packed with an older Python than expected - one that no longer
gets fixes, with the OpenSSL of its day - does not pass: exits 1.
"""

from __future__ import annotations

import platform
import re
import sys

# The oldest Python a published program may carry: one that still gets
# installers for Windows and macOS with fixes (ci.yml builds with it).
MINIMUM = (3, 14, 0)


def problem(selfcheck: str, built: tuple[int, int, int], minimum: tuple[int, int, int] = MINIMUM) -> str:
    """What is wrong with the Python ``selfcheck`` names, or ""."""
    found = re.search(r"\bPython (\d+)\.(\d+)\.(\d+)\b", selfcheck)
    if not found:
        return "the program did not say which Python it runs on"
    bundled = tuple(int(part) for part in found.groups())
    shown = ".".join(map(str, bundled))
    if bundled < minimum:
        return f"the program carries Python {shown}, older than {'.'.join(map(str, minimum))}"
    if bundled != tuple(built):
        return f"the program carries Python {shown}, but was built with {'.'.join(map(str, built))}"
    return ""


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    built = tuple(int(part) for part in platform.python_version_tuple()[:3])
    wrong = problem(argv[0], built)  # type: ignore[arg-type]
    if wrong:
        print(f"::error::{wrong}")
        return 1
    print(f"the program carries Python {platform.python_version()}, as built")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
