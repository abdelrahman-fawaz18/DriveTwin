"""Qualify bounded history SQL and frozen pagination against owned real services."""

import sys

from extension_fixture import main

if __name__ == "__main__":
    sys.argv.append("--history-backend")
    raise SystemExit(main())
