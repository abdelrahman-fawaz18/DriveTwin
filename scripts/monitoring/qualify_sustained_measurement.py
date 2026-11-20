"""Run revised short-run W0/W1 acceptance, never sustained-load qualification."""

from qualify_extension import main

if __name__ == "__main__":
    raise SystemExit(main(workload_measurement=True, sustained=True))
