"""Windows entry point for the short P1-E2-B instrumented rehearsal."""

from qualify_extension import main

if __name__ == "__main__":
    raise SystemExit(main(workload_measurement=True))
