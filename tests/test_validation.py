import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from jp_ohlcv.validation import validate_parquet


def write_dataset(path, timestamps, opens, highs, lows, closes, volumes):
    timestamp_values = pd.to_datetime(timestamps, utc=True)

    table = pa.table({
        "timestamp": pa.array(
            timestamp_values,
            type=pa.timestamp("ns", tz="UTC"),
        ),
        "open": pa.array(opens, type=pa.float64()),
        "high": pa.array(highs, type=pa.float64()),
        "low": pa.array(lows, type=pa.float64()),
        "close": pa.array(closes, type=pa.float64()),
        "volume": pa.array(volumes, type=pa.float64()),
    })
    pq.write_table(table, path)


def valid_values():
    timestamps = [
        "2024-01-02T10:00:00Z",
        "2024-01-02T10:01:00Z",
        "2024-01-02T10:02:00Z",
    ]
    return (
        timestamps,
        [1.10, 1.11, 1.12],
        [1.12, 1.13, 1.14],
        [1.09, 1.10, 1.11],
        [1.11, 1.12, 1.13],
        [10.0, 12.0, 8.0],
    )


def test_valid_dataset(tmp_path):
    path = tmp_path / "valid.parquet"
    write_dataset(path, *valid_values())

    result = validate_parquet(path, expected_year=2024)

    assert result["valid"] is True
    assert result["rows"] == 3
    assert result["gap_intervals"] == 0
    assert result["missing_minutes"] == 0
    assert result["largest_gap_minutes"] == 0
    assert result["missing_minute_gaps"] == 0
    assert len(result["sha256"]) == 64


def test_single_missing_minute_is_reported(tmp_path):
    path = tmp_path / "gap.parquet"
    values = valid_values()
    values = (
        [
            "2024-01-02T10:00:00Z",
            "2024-01-02T10:02:00Z",
            "2024-01-02T10:03:00Z",
        ],
        *values[1:],
    )
    write_dataset(path, *values)

    result = validate_parquet(path, expected_year=2024)

    assert result["gap_intervals"] == 1
    assert result["missing_minutes"] == 1
    assert result["largest_gap_minutes"] == 1
    assert result["missing_minute_gaps"] == 1


def test_multiple_gaps_and_largest_gap(tmp_path):
    path = tmp_path / "multiple-gaps.parquet"
    values = valid_values()
    values = (
        [
            "2024-01-02T10:00:00Z",
            "2024-01-02T10:03:00Z",
            "2024-01-02T10:10:00Z",
        ],
        *values[1:],
    )
    write_dataset(path, *values)

    result = validate_parquet(path, expected_year=2024)

    assert result["gap_intervals"] == 2
    assert result["missing_minutes"] == 8
    assert result["largest_gap_minutes"] == 6


def test_gap_across_batch_boundary(tmp_path):
    path = tmp_path / "batch-boundary-gap.parquet"

    timestamps = [
        "2024-01-02T10:00:00Z",
        "2024-01-02T10:01:00Z",
        "2024-01-02T10:05:00Z",
        "2024-01-02T10:06:00Z",
    ]

    write_dataset(
        path,
        timestamps,
        [1.10, 1.11, 1.12, 1.13],
        [1.12, 1.13, 1.14, 1.15],
        [1.09, 1.10, 1.11, 1.12],
        [1.11, 1.12, 1.13, 1.14],
        [10.0, 12.0, 8.0, 9.0],
    )

    result = validate_parquet(
        path,
        expected_year=2024,
        batch_size=2,
    )

    assert result["rows"] == 4
    assert result["gap_intervals"] == 1
    assert result["missing_minutes"] == 3
    assert result["largest_gap_minutes"] == 3


def test_strict_gap_check_fails(tmp_path):
    path = tmp_path / "gap.parquet"
    values = valid_values()
    values = (
        [
            "2024-01-02T10:00:00Z",
            "2024-01-02T10:02:00Z",
            "2024-01-02T10:03:00Z",
        ],
        *values[1:],
    )
    write_dataset(path, *values)

    with pytest.raises(ValueError, match="gap intervals"):
        validate_parquet(path, expected_year=2024, strict_gaps=True)


def test_duplicate_timestamps_fail(tmp_path):
    path = tmp_path / "duplicate.parquet"
    values = valid_values()
    values = (
        [
            "2024-01-02T10:00:00Z",
            "2024-01-02T10:00:00Z",
            "2024-01-02T10:02:00Z",
        ],
        *values[1:],
    )
    write_dataset(path, *values)

    with pytest.raises(ValueError, match="strictly increasing"):
        validate_parquet(path, expected_year=2024)


def test_invalid_ohlc_fails(tmp_path):
    path = tmp_path / "invalid.parquet"
    values = valid_values()
    values = (
        values[0],
        values[1],
        [1.05, 1.13, 1.14],
        values[3],
        values[4],
        values[5],
    )
    write_dataset(path, *values)

    with pytest.raises(ValueError, match="High is below"):
        validate_parquet(path, expected_year=2024)


def test_wrong_year_fails(tmp_path):
    path = tmp_path / "wrong-year.parquet"
    write_dataset(path, *valid_values())

    with pytest.raises(ValueError, match="outside 2025"):
        validate_parquet(path, expected_year=2025)
