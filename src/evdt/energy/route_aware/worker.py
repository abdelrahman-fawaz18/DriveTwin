"""One bounded request on standard input, one JSON result on standard output."""

import sys

from .forecast import forecast, parse_result
from .wire import MAX_BYTES, canonical_bytes, parse_request


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    try:
        request = parse_request(raw)
    except ValueError:
        print("invalid_forecast_request", file=sys.stderr)
        return 2
    try:
        result = forecast(request)
        output = canonical_bytes(result)
        parse_result(output)
    except (ArithmeticError, ValueError):
        print("forecast_calculation_integrity_failure", file=sys.stderr)
        return 3
    sys.stdout.buffer.write(output + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
