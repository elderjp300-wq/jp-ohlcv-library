#!/usr/bin/env python3
"""Live compatibility test for the Dukascopy Downloader CLI."""

from __future__ import annotations

import argparse
import json
import numpy as np
import subprocess
import sys
from pathlib import Path

import pandas as pd


INSTRUMENTS = {
    "EURUSD": {"provider_symbol": "EURUSD", "search": "EURUSD"},
    "GBPUSD": {"provider_symbol": "GBPUSD", "search": "GBPUSD"},
    "USDJPY": {"provider_symbol": "USDJPY", "search": "USDJPY"},
    "XAUUSD": {"provider_symbol": "XAUUSD", "search": "XAUUSD"},
    "US30": {
        "provider_symbol": "USA30.IDX-USD",
        "search": "USA30",
    },
    "US100": {
        "provider_symbol": "USATECH.IDX-USD",
        "search": "USATECH",
    },
}

OHLC = ["open", "high", "low", "close"]


def run_command(args: list[str], log_path: Path, timeout: int = 300) -> dict:
    """Run a command, preserve combined output, and return its result."""
    command = [str(value) for value in args]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        code = result.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""

        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")

        output = stdout + stderr + f"\nTIMEOUT after {timeout} seconds\n"
        code = None
        timed_out = True

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n$ " + " ".join(command) + "\n")
        log.write(output)
        if not output.endswith("\n"):
            log.write("\n")

    print(f"$ {' '.join(command)}")
    if output.strip():
        print(output[-5000:])

    return {
        "command": command,
        "return_code": code,
        "timed_out": timed_out,
        "output": output,
        "success": code == 0 and not timed_out,
    }


def inspect_parquet(path: Path, symbol: str, sample_date: str) -> dict:
    """Inspect exported candles without silently cleaning the sample."""
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError("Export file is missing or empty.")

    frame = pd.read_parquet(path)

    if isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.reset_index()

    frame.columns = [str(column).strip().lower() for column in frame.columns]

    aliases = {
        "datetime": "time",
        "timestamp": "time",
        "date": "time",
        "date_time": "time",
        "vol": "volume",
    }

    rename = {}
    for old, new in aliases.items():
        if old in frame.columns and new not in frame.columns:
            rename[old] = new

    frame = frame.rename(columns=rename)

    required = ["time", *OHLC, "volume"]
    missing = [column for column in required if column not in frame.columns]

    if missing:
        raise ValueError(
            f"Missing columns: {missing}. Found: {list(frame.columns)}"
        )

    timestamps = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    numeric = frame[OHLC + ["volume"]].apply(
        pd.to_numeric, errors="coerce"
    )

    invalid_timestamp_count = int(timestamps.isna().sum())
    invalid_numeric_count = int(numeric.isna().any(axis=1).sum())

    valid_timestamps = timestamps.dropna()
    expected_start = pd.Timestamp(sample_date, tz="UTC")
    expected_end = expected_start + pd.Timedelta(days=1)

    outside_date_count = int(
        (
            (valid_timestamps < expected_start)
            | (valid_timestamps >= expected_end)
        ).sum()
    )

    duplicate_count = int(timestamps.duplicated().sum())
    monotonic = bool(timestamps.dropna().is_monotonic_increasing)

    values = numeric.to_numpy(dtype=float)
    finite_rows = int((~np.isfinite(values).all(axis=1)).sum())

    high_low_valid = numeric["high"] >= numeric["low"]
    high_valid = numeric["high"] >= numeric[["open", "close"]].max(axis=1)
    low_valid = numeric["low"] <= numeric[["open", "close"]].min(axis=1)
    positive_prices = (numeric[OHLC] > 0).all(axis=1)
    nonnegative_volume = numeric["volume"] >= 0

    invalid_ohlc_count = int(
        (~(high_low_valid & high_valid & low_valid & positive_prices)).sum()
    )
    negative_volume_count = int((~nonnegative_volume).sum())

    checks = {
        "has_rows": len(frame) > 0,
        "no_invalid_timestamps": invalid_timestamp_count == 0,
        "timestamps_within_sample_date": outside_date_count == 0,
        "no_duplicate_timestamps": duplicate_count == 0,
        "timestamps_monotonic": monotonic,
        "no_missing_numeric_values": invalid_numeric_count == 0,
        "finite_numeric_values": finite_rows == 0,
        "valid_ohlc": invalid_ohlc_count == 0,
        "nonnegative_volume": negative_volume_count == 0,
    }

    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise ValueError(
            f"Parquet validation failed: {failed}. "
            f"Rows={len(frame)}, duplicates={duplicate_count}, "
            f"invalid_timestamps={invalid_timestamp_count}, "
            f"invalid_ohlc={invalid_ohlc_count}, "
            f"negative_volume={negative_volume_count}"
        )

    return {
        "symbol": symbol,
        "rows": int(len(frame)),
        "file_size_bytes": int(path.stat().st_size),
        "columns": list(frame.columns),
        "dtypes": {name: str(dtype) for name, dtype in frame.dtypes.items()},
        "first_timestamp_utc": str(timestamps.min()),
        "last_timestamp_utc": str(timestamps.max()),
        "duplicate_timestamps": duplicate_count,
        "invalid_ohlc_rows": invalid_ohlc_count,
        "zero_volume_rows": int((numeric["volume"] == 0).sum()),
        "flat_candle_rows": int(
            (
                (numeric["open"] == numeric["high"])
                & (numeric["high"] == numeric["low"])
                & (numeric["low"] == numeric["close"])
            ).sum()
        ),
        "price_min": float(numeric[OHLC].min().min()),
        "price_max": float(numeric[OHLC].max().max()),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2025-01-06")
    parser.add_argument(
        "--output-dir",
        default="downloader-compatibility-report",
    )
    args = parser.parse_args()

    sample_date = pd.Timestamp(args.date)
    if sample_date.tzinfo is not None:
        raise ValueError("Pass --date as a date without a timezone.")
    sample_date = sample_date.strftime("%Y-%m-%d")

    output_dir = Path(args.output_dir)
    cache_dir = output_dir / "cache"
    exports_dir = output_dir / "exports"
    logs_dir = output_dir / "logs"

    for folder in [cache_dir, exports_dir, logs_dir]:
        folder.mkdir(parents=True, exist_ok=True)

    version_result = run_command(
        ["duka", "--version"],
        logs_dir / "version.log",
    )

    if not version_result["success"]:
        raise RuntimeError("The duka CLI is not available.")

    print("\nDUKASCOPY DOWNLOADER VERSION")
    print(version_result["output"].strip())

    results = []

    for asset, config in INSTRUMENTS.items():
        provider_symbol = config["provider_symbol"]
        print("\n" + "=" * 68)
        print(f"TESTING {asset} ({provider_symbol})")
        print("=" * 68)

        asset_log = logs_dir / f"{asset}.log"
        export_path = exports_dir / f"{asset}_M1_{sample_date}.parquet"

        result = {
            "asset": asset,
            "provider_symbol": provider_symbol,
            "sample_date": sample_date,
            "status": "FAILED",
        }

        # Discovery is informational; a search failure does not block
        # the direct metadata and download tests.
        discovery = run_command(
            ["duka", "instruments", "--search", config["search"]],
            asset_log,
        )
        result["instrument_search_success"] = discovery["success"]
        result["instrument_search_output"] = discovery["output"][-12000:]

        info = run_command(
            ["duka", "info", provider_symbol],
            asset_log,
        )
        result["instrument_info_success"] = info["success"]
        result["instrument_info_output"] = info["output"][-12000:]

        download = run_command(
            [
                "duka", "download", provider_symbol,
                "--tf", "m1",
                "--from", sample_date,
                "--to", sample_date,
                "--side", "BID",
                "--data-dir", str(cache_dir),
                "--rate", "2",
            ],
            asset_log,
            timeout=600,
        )
        result["download_success"] = download["success"]
        result["download_return_code"] = download["return_code"]

        if not download["success"]:
            result["error"] = "Download command failed or timed out."
            results.append(result)
            continue

        export = run_command(
            [
                "duka", "export", provider_symbol,
                "--from", sample_date,
                "--to", sample_date,
                "--side", "BID",
                "--data-dir", str(cache_dir),
                "-o", str(export_path),
            ],
            asset_log,
            timeout=300,
        )
        result["export_success"] = export["success"]

        if not export["success"]:
            result["error"] = "Parquet export command failed."
            results.append(result)
            continue

        try:
            result["parquet"] = inspect_parquet(
                export_path, asset, sample_date
            )
            result["status"] = "PASS"
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"

        results.append(result)
        print(f"\nRESULT: {asset} -> {result['status']}")

    report = {
        "source": "Dukascopy",
        "downloader": "dukascopy-downloader",
        "downloader_version": version_result["output"].strip(),
        "timeframe": "M1",
        "price_side": "BID",
        "timezone": "UTC",
        "sample_date": sample_date,
        "production_data_published": False,
        "results": results,
        "passed": sum(item["status"] == "PASS" for item in results),
        "failed": sum(item["status"] != "PASS" for item in results),
    }

    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )

    lines = [
        "# Dukascopy Downloader Compatibility",
        "",
        f"- Downloader: `{report['downloader_version']}`",
        f"- Sample date: `{sample_date}`",
        "- Timeframe: `M1`",
        "- Price side: `BID`",
        "- Timezone: `UTC`",
        "- Production publishing: **disabled**",
        "",
        "| Asset | Provider symbol | Instrument info | Download | Export | Result |",
        "|---|---|---:|---:|---:|---:|",
    ]

    for item in results:
        lines.append(
            f"| {item['asset']} | `{item['provider_symbol']}` | "
            f"{'PASS' if item.get('instrument_info_success') else 'CHECK'} | "
            f"{'PASS' if item.get('download_success') else 'FAIL'} | "
            f"{'PASS' if item.get('export_success') else 'FAIL'} | "
            f"**{item['status']}** |"
        )

    lines.extend([
        "",
        f"**Passed: {report['passed']}/{len(results)}**",
        f"**Failed: {report['failed']}/{len(results)}**",
        "",
        "Full command logs and Parquet samples are included in the workflow artifact.",
    ])

    summary = "\n".join(lines)
    (output_dir / "summary.md").write_text(summary, encoding="utf-8")
    print("\n" + summary)
    print(f"\nJSON report: {report_path}")

    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
