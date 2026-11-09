#!/usr/bin/env python3

import argparse
import datetime as dt
import lzma
import math
import struct
import sys

import requests


BASE_URL = "https://datafeed.dukascopy.com/datafeed"

# Public library names mapped to Dukascopy's source symbols.
INSTRUMENTS = {
    "EURUSD": {"symbol": "EURUSD", "scale": 100000},
    "GBPUSD": {"symbol": "GBPUSD", "scale": 100000},
    "USDJPY": {"symbol": "USDJPY", "scale": 1000},
    "XAUUSD": {"symbol": "XAUUSD", "scale": 1000},
    "US30": {"symbol": "USA30IDXUSD", "scale": None},
    "US100": {"symbol": "USATECHIDXUSD", "scale": None},
}

RECORD = struct.Struct(">IIIff")
RECORD_SIZE = RECORD.size
REQUEST_TIMEOUT = 20


def build_url(symbol, date, hour):
    return (
        f"{BASE_URL}/{symbol}/"
        f"{date.year}/{date.month - 1:02d}/{date.day:02d}/"
        f"{hour:02d}h_ticks.bi5"
    )


def inspect_instrument(session, asset, date):
    config = INSTRUMENTS[asset]
    symbol = config["symbol"]
    scale = config["scale"]

    report = {
        "asset": asset,
        "symbol": symbol,
        "scale": scale,
        "files_found": 0,
        "files_missing": 0,
        "records": 0,
        "raw_min": None,
        "raw_max": None,
        "ask_volume_min": None,
        "ask_volume_max": None,
        "bid_volume_min": None,
        "bid_volume_max": None,
        "first_tick": None,
        "last_tick": None,
        "errors": [],
    }

    for hour in range(24):
        url = build_url(symbol, date, hour)

        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)

            if response.status_code == 404:
                report["files_missing"] += 1
                continue

            response.raise_for_status()

            if not response.content:
                report["files_missing"] += 1
                continue

            raw = lzma.decompress(response.content)

            if len(raw) % RECORD_SIZE != 0:
                raise ValueError(
                    f"Decompressed size {len(raw)} is not divisible "
                    f"by {RECORD_SIZE}"
                )

            report["files_found"] += 1

            for offset in range(0, len(raw), RECORD_SIZE):
                ms, ask, bid, ask_volume, bid_volume = RECORD.unpack_from(
                    raw, offset
                )

                if ms >= 3_600_000:
                    raise ValueError(f"Invalid hour offset: {ms}")

                if ask <= 0 or bid <= 0 or ask < bid:
                    raise ValueError(
                        f"Invalid quote: ask={ask}, bid={bid}"
                    )

                if not math.isfinite(ask_volume) or ask_volume < 0:
                    raise ValueError(f"Invalid ask volume: {ask_volume}")

                if not math.isfinite(bid_volume) or bid_volume < 0:
                    raise ValueError(f"Invalid bid volume: {bid_volume}")

                timestamp = (
                    dt.datetime.combine(
                        date,
                        dt.time(hour=hour),
                        tzinfo=dt.timezone.utc,
                    )
                    + dt.timedelta(milliseconds=ms)
                )

                if report["first_tick"] is None:
                    report["first_tick"] = timestamp.isoformat()

                report["last_tick"] = timestamp.isoformat()
                report["records"] += 1

                low = min(ask, bid)
                high = max(ask, bid)

                report["raw_min"] = (
                    low if report["raw_min"] is None
                    else min(report["raw_min"], low)
                )
                report["raw_max"] = (
                    high if report["raw_max"] is None
                    else max(report["raw_max"], high)
                )

                report["ask_volume_min"] = (
                    ask_volume
                    if report["ask_volume_min"] is None
                    else min(report["ask_volume_min"], ask_volume)
                )
                report["ask_volume_max"] = (
                    ask_volume
                    if report["ask_volume_max"] is None
                    else max(report["ask_volume_max"], ask_volume)
                )
                report["bid_volume_min"] = (
                    bid_volume
                    if report["bid_volume_min"] is None
                    else min(report["bid_volume_min"], bid_volume)
                )
                report["bid_volume_max"] = (
                    bid_volume
                    if report["bid_volume_max"] is None
                    else max(report["bid_volume_max"], bid_volume)
                )

        except Exception as exc:
            report["errors"].append(f"hour={hour}: {type(exc).__name__}: {exc}")

    if scale is not None and report["raw_min"] is not None:
        report["scaled_min"] = report["raw_min"] / scale
        report["scaled_max"] = report["raw_max"] / scale
    else:
        report["scaled_min"] = None
        report["scaled_max"] = None

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Inspect Dukascopy historical tick samples."
    )
    parser.add_argument(
        "--date",
        default="2025-01-06",
        help="Historical sample date (2023-2025 only).",
    )
    args = parser.parse_args()

    try:
        sample_date = dt.date.fromisoformat(args.date)
    except ValueError:
        parser.error("Date must use YYYY-MM-DD format.")

    if sample_date.year not in (2023, 2024, 2025):
        parser.error("Only 2023, 2024 and 2025 sample dates are permitted.")

    print("=" * 76)
    print("JP OHLCV LIBRARY — DUKASCOPY SOURCE INSPECTION")
    print(f"Sample date: {sample_date.isoformat()} UTC")
    print(f"Record format: {RECORD.format}, {RECORD_SIZE} bytes")
    print("=" * 76)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "JP-OHLCV-Library/0.1 source-inspection"
    })

    reports = []

    for asset in INSTRUMENTS:
        print(f"\nInspecting {asset}...", flush=True)
        report = inspect_instrument(session, asset, sample_date)
        reports.append(report)

        print(f"  Dukascopy symbol: {report['symbol']}")
        print(f"  Files found:      {report['files_found']}/24")
        print(f"  Files missing:    {report['files_missing']}/24")
        print(f"  Tick records:     {report['records']:,}")
        print(f"  Raw price range:  {report['raw_min']} - {report['raw_max']}")

        if report["scaled_min"] is not None:
            print(
                f"  Scaled range:     "
                f"{report['scaled_min']} - {report['scaled_max']}"
            )
        else:
            print("  Scaled range:     NOT ASSIGNED — requires verification")

        print(
            f"  Ask volume range: "
            f"{report['ask_volume_min']} - {report['ask_volume_max']}"
        )
        print(
            f"  Bid volume range: "
            f"{report['bid_volume_min']} - {report['bid_volume_max']}"
        )
        print(f"  First tick:       {report['first_tick']}")
        print(f"  Last tick:        {report['last_tick']}")

        if report["errors"]:
            print(f"  Errors:           {len(report['errors'])}")
            for error in report["errors"][:5]:
                print(f"    - {error}")

    print("\n" + "=" * 76)
    print("FINAL SUMMARY")
    print("=" * 76)

    successful = 0

    for report in reports:
        passed = report["records"] > 0 and not report["errors"]

        if passed:
            successful += 1

        print(
            f"{report['asset']:8} "
            f"{'PASS' if passed else 'CHECK'} "
            f"| {report['records']:>10,} ticks "
            f"| {report['symbol']}"
        )

    print(f"\nSuccessful instruments: {successful}/{len(INSTRUMENTS)}")
    print("No datasets were generated.")
    print("No catalog entries were changed.")

    if successful != len(INSTRUMENTS):
        sys.exit(1)


if __name__ == "__main__":
    main()
