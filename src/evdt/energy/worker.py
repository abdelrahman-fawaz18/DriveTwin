"""One bounded stdin request and stdout result, with no dataset or plant access."""

import json
import sys

from evdt.energy.jobs import MAX_JOB_BYTES, run_request


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_JOB_BYTES + 1)
        result = run_request(raw)
    except (ValueError, TypeError, OverflowError, RecursionError):
        print(
            json.dumps(
                {
                    "version": "isolated-energy-result/1.0.0",
                    "status": "rejected",
                    "reason": "invalid_job",
                }
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
