from __future__ import annotations

import argparse
import json
import sys

from jp_ohlcv.validation import validate_parquet


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a JP OHLCV Parquet dataset."
    )
    parser.add_argument("file", help="Path to the Parquet file")
    parser.add_argument("--year", type=int, help="Expected UTC calendar year")
    parser.add_argument(
        "--strict-gaps",
        action="store_true",
        help="Treat missing-minute gaps as validation failures",
    )
    args = parser.parse_args()

    try:
        result = validate_parquet(
            args.file,
            expected_year=args.year,
            strict_gaps=args.strict_gaps,
        )
    except Exception as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
