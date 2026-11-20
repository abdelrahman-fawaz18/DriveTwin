"""Run the owned P1-D3 service fixture inside Linux through scripts/linux.py."""

import sys

from extension_fixture import main

if __name__ == "__main__":
    sys.argv.append("--recovery")
    raise SystemExit(main())
