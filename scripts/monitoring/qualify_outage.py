"""Use the shared isolated bootstrap for P1-E1, from Linux through scripts/linux.py."""

import sys

from extension_fixture import main

if __name__ == "__main__":
    sys.argv.append("--outage")
    raise SystemExit(main())
