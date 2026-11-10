from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


REQUIRED_COLUMNS = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

PRICE_COLUMNS = ["open", "high", "low", "close"]
MINUTE_NS = 60_000_000_000


def validate_parquet(
    path: str | Path,
    expected_year: int | None = None,
    strict_gaps: bool = False,
    batch_size: int = 100_000,
) -> dict:
    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(path)

    parquet = pq.ParquetFile(path)
    schema = parquet.schema_arrow

    missing = [name for name in REQUIRED_COLUMNS if name not in schema.names]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    timestamp_type = schema.field("timestamp").type
    if not pa.types.is_timestamp(timestamp_type):
        raise ValueError("timestamp must use an Arrow timestamp type")

    if timestamp_type.tz != "UTC":
        raise ValueError("timestamp timezone must be UTC")

    for name in PRICE_COLUMNS + ["volume"]:
        if not pa.types.is_floating(schema.field(name).type):
            raise ValueError(f"{name} must use a floating-point type")

    rows = 0
    gap_intervals = 0
    missing_minutes = 0
    largest_gap_minutes = 0

    previous_ns = None
    first_timestamp = None
    last_timestamp = None

    for batch in parquet.iter_batches(
        columns=REQUIRED_COLUMNS,
        batch_size=batch_size,
    ):
        frame = batch.to_pandas()

        if frame.isnull().any().any():
            raise ValueError("Dataset contains null values")

        timestamps = pd.to_datetime(frame["timestamp"], utc=True)

        if timestamps.empty:
            continue

        if expected_year is not None:
            if not (timestamps.dt.year == expected_year).all():
                raise ValueError(
                    f"Dataset contains timestamps outside {expected_year}"
                )

        ns = timestamps.astype("int64").to_numpy()

        if np.any(ns % MINUTE_NS != 0):
            raise ValueError("Timestamps must align to exact minute boundaries")

        differences = np.diff(ns)

        if previous_ns is not None:
            differences = np.concatenate(
                ([ns[0] - previous_ns], differences)
            )

        if np.any(differences <= 0):
            raise ValueError("Timestamps must be strictly increasing and unique")

        gap_differences = differences[differences > MINUTE_NS]

        if gap_differences.size:
            gap_sizes = gap_differences // MINUTE_NS - 1

            gap_intervals += int(gap_sizes.size)
            missing_minutes += int(gap_sizes.sum())
            largest_gap_minutes = max(
                largest_gap_minutes,
                int(gap_sizes.max()),
            )

        previous_ns = int(ns[-1])

        if first_timestamp is None:
            first_timestamp = timestamps.iloc[0].isoformat()

        last_timestamp = timestamps.iloc[-1].isoformat()

        prices = frame[PRICE_COLUMNS].to_numpy(dtype=np.float64)
        volume = frame["volume"].to_numpy(dtype=np.float64)

        if not np.isfinite(prices).all():
            raise ValueError("Prices contain non-finite values")

        if not np.isfinite(volume).all():
            raise ValueError("Volume contains non-finite values")

        if np.any(prices <= 0):
            raise ValueError("Prices must be positive")

        if np.any(volume < 0):
            raise ValueError("Volume cannot be negative")

        high = frame["high"].to_numpy(dtype=np.float64)
        low = frame["low"].to_numpy(dtype=np.float64)
        open_ = frame["open"].to_numpy(dtype=np.float64)
        close = frame["close"].to_numpy(dtype=np.float64)

        if np.any(high < np.maximum(open_, close)):
            raise ValueError("High is below open or close")

        if np.any(low > np.minimum(open_, close)):
            raise ValueError("Low is above open or close")

        if np.any(high < low):
            raise ValueError("High is below low")

        rows += len(frame)

    if rows == 0:
        raise ValueError("Dataset contains no rows")

    if strict_gaps and gap_intervals:
        raise ValueError(
            f"Dataset contains {gap_intervals} gap intervals "
            f"and {missing_minutes} missing minutes"
        )

    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return {
        "valid": True,
        "rows": rows,
        "size_bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "gap_intervals": gap_intervals,
        "missing_minutes": missing_minutes,
        "largest_gap_minutes": largest_gap_minutes,
        "missing_minute_gaps": gap_intervals,
    }
