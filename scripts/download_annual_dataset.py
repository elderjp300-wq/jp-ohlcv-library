#!/usr/bin/env python3
"""Download, normalize, validate and register one annual Dukascopy M1 dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from jp_ohlcv.validation import validate_parquet


DOWNLOADER_VERSION = "1.1.0"

INSTRUMENTS = {
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "XAUUSD": "XAUUSD",
    "US30": "USA30.IDX-USD",
    "US100": "USATECH.IDX-USD",
}

YEARS = (2023, 2024, 2025)

PRICE_BASIS = "Dukascopy BID candles"
VOLUME_TYPE = (
    "Dukascopy provider-reported candle volume; "
    "semantics not independently verified"
)

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume"]


def run_command(command: list[str], log_path: Path, timeout: int) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n$ " + " ".join(command) + "\n")
        log.flush()

        try:
            result = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Command timed out after {timeout}s: {command[0]}"
            ) from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {command[0]}. "
            f"Inspect {log_path}"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def normalize_export(source: Path, destination: Path, year: int) -> None:
    if not source.is_file() or source.stat().st_size == 0:
        raise ValueError("Downloader export is missing or empty.")

    frame = pd.read_parquet(source)

    if isinstance(frame.index, pd.DatetimeIndex) and not any(
        str(column).strip().lower() in ("time", "timestamp", "datetime", "date")
        for column in frame.columns
    ):
        frame = frame.reset_index()

    frame.columns = [str(column).strip().lower() for column in frame.columns]

    aliases = {
        "time": "timestamp",
        "datetime": "timestamp",
        "date": "timestamp",
        "date_time": "timestamp",
        "vol": "volume",
    }

    rename = {}
    for old, new in aliases.items():
        if old in frame.columns and new not in frame.columns:
            rename[old] = new

    frame = frame.rename(columns=rename)

    missing = [column for column in COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(
            f"Export is missing columns {missing}. "
            f"Found: {list(frame.columns)}"
        )

    frame = frame[COLUMNS].copy()

    timestamps = pd.to_datetime(
        frame["timestamp"],
        utc=True,
        errors="coerce",
    )

    if timestamps.isna().any():
        raise ValueError("Export contains invalid timestamps.")

    if not (timestamps.dt.year == year).all():
        raise ValueError(f"Export contains timestamps outside {year}.")

    if timestamps.ne(timestamps.dt.floor("min")).any():
        raise ValueError("Export contains timestamps not aligned to minutes.")

    if timestamps.duplicated().any():
        raise ValueError("Duplicate timestamps detected. No rows were removed.")

    if not timestamps.is_monotonic_increasing:
        raise ValueError("Timestamps are not in chronological order.")

    frame["timestamp"] = timestamps

    for column in NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    values = frame[NUMERIC_COLUMNS].to_numpy(dtype=np.float64)

    if not np.isfinite(values).all():
        raise ValueError("Numeric fields contain null or non-finite values.")

    prices = frame[["open", "high", "low", "close"]].to_numpy(
        dtype=np.float64
    )
    volume = frame["volume"].to_numpy(dtype=np.float64)

    if np.any(prices <= 0):
        raise ValueError("Prices must be positive.")

    if np.any(volume < 0):
        raise ValueError("Volume cannot be negative.")

    high = frame["high"].to_numpy(dtype=np.float64)
    low = frame["low"].to_numpy(dtype=np.float64)
    open_ = frame["open"].to_numpy(dtype=np.float64)
    close = frame["close"].to_numpy(dtype=np.float64)

    if np.any(high < np.maximum(open_, close)):
        raise ValueError("High is below open or close.")

    if np.any(low > np.minimum(open_, close)):
        raise ValueError("Low is above open or close.")

    if np.any(high < low):
        raise ValueError("High is below low.")

    if frame.empty:
        raise ValueError("Export contains no candles.")

    schema = pa.schema([
        pa.field("timestamp", pa.timestamp("ns", tz="UTC"), nullable=False),
        pa.field("open", pa.float64(), nullable=False),
        pa.field("high", pa.float64(), nullable=False),
        pa.field("low", pa.float64(), nullable=False),
        pa.field("close", pa.float64(), nullable=False),
        pa.field("volume", pa.float64(), nullable=False),
    ])

    table = pa.Table.from_pandas(
        frame,
        schema=schema,
        preserve_index=False,
        safe=True,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)

    temporary = destination.with_name(destination.name + ".tmp")

    try:
        pq.write_table(
            table,
            temporary,
            compression="snappy",
            version="2.6",
        )

        # Verify the exact physical schema before validation.
        actual_schema = pq.read_schema(temporary)
        if not actual_schema.equals(schema, check_metadata=False):
            raise ValueError(f"Unexpected output schema: {actual_schema}")

        # Full read-back validation before publication.
        validate_parquet(temporary, expected_year=year)

        os.replace(temporary, destination)

    finally:
        if temporary.exists():
            temporary.unlink()


def update_catalog(
    catalog_path: Path,
    asset: str,
    year: int,
    dataset_path: Path,
    validation: dict,
) -> None:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

    relative_path = dataset_path.as_posix()

    existing = [
        item for item in catalog["datasets"]
        if item["asset"] == asset and item["year"] == year
    ]

    if existing:
        raise ValueError(
            f"Catalog already contains {asset} {year}; refusing duplicate."
        )

    entry = {
        "asset": asset,
        "year": year,
        "timeframe": "1m",
        "format": "parquet",
        "path": relative_path,
        "rows": validation["rows"],
        "size_bytes": validation["size_bytes"],
        "sha256": validation["sha256"],
        "first_timestamp": validation["first_timestamp"],
        "last_timestamp": validation["last_timestamp"],
        "price_basis": PRICE_BASIS,
        "volume_type": VOLUME_TYPE,
    }

    catalog["datasets"].append(entry)
    catalog["datasets"].sort(key=lambda item: (item["asset"], item["year"]))

    temporary = catalog_path.with_name(catalog_path.name + ".tmp")

    try:
        temporary.write_text(
            json.dumps(catalog, indent=2) + "\n",
            encoding="utf-8",
        )
        json.loads(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, catalog_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Produce one validated annual Dukascopy M1 dataset."
    )

    parser.add_argument("--asset", required=True, choices=INSTRUMENTS)
    parser.add_argument("--year", required=True, type=int, choices=YEARS)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--repository", type=Path, default=Path("."))

    args = parser.parse_args()

    asset = args.asset
    year = args.year
    symbol = INSTRUMENTS[asset]

    repository = args.repository.resolve()
    cache_dir = args.cache_dir.resolve()
    report_path = args.report.resolve()

    destination = repository / "data" / asset / str(year) / "1min.parquet"
    export_path = report_path.parent / f"{asset}_{year}_export.parquet"
    log_path = report_path.parent / f"{asset}_{year}.log"

    report_path.parent.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "asset": asset,
        "year": year,
        "provider": "Dukascopy",
        "provider_symbol": symbol,
        "downloader_version": DOWNLOADER_VERSION,
        "timeframe": "M1",
        "price_side": "BID",
        "timezone": "UTC",
        "published": False,
    }

    try:
        if destination.exists():
            raise FileExistsError(
                f"{destination} already exists. Refusing to overwrite."
            )

        if not (repository / "data" / "index.json").is_file():
            raise FileNotFoundError("data/index.json is missing.")

        version = subprocess.run(
            ["duka", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )

        report["actual_downloader_version"] = (
            version.stdout.strip() or version.stderr.strip()
        )

        if DOWNLOADER_VERSION not in report["actual_downloader_version"]:
            raise RuntimeError(
                "Installed downloader version does not match the pinned version."
            )

        start = f"{year}-01-01"
        end = f"{year}-12-31"

        run_command(
            [
                "duka", "download", symbol,
                "--tf", "m1",
                "--from", start,
                "--to", end,
                "--side", "BID",
                "--data-dir", str(cache_dir),
                "--rate", "2",
            ],
            log_path,
            timeout=7200,
        )

        run_command(
            [
                "duka", "export", symbol,
                "--from", start,
                "--to", end,
                "--side", "BID",
                "--data-dir", str(cache_dir),
                "-o", str(export_path),
            ],
            log_path,
            timeout=1800,
        )

        normalize_export(export_path, destination, year)

        validation = validate_parquet(destination, expected_year=year)

        update_catalog(
            repository / "data" / "index.json",
            asset,
            year,
            destination.relative_to(repository),
            validation,
        )

        report.update(validation)
        report["published"] = True
        report["price_basis"] = PRICE_BASIS
        report["volume_type"] = VOLUME_TYPE
        report["missing_minute_gaps"] = validation["missing_minute_gaps"]

        print(json.dumps(report, indent=2))
        return 0

    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(report, indent=2), file=sys.stderr)
        return 1

    finally:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    sys.exit(main())
