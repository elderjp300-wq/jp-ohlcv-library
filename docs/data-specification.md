# Data Specification

## Target dataset layout

`data/ASSET/YEAR/1min.parquet`

Example: `data/EURUSD/2024/1min.parquet`

## Target columns

| Column | Intended type | Description |
|---|---|---|
| timestamp | UTC timestamp | Opening time of the one-minute bar |
| open | float64 | First price in the interval |
| high | float64 | Highest price in the interval |
| low | float64 | Lowest price in the interval |
| close | float64 | Last price in the interval |
| volume | float64 | Source-defined volume measure |

The final physical Parquet schema will be fixed before production publication.

## Timestamp rules

- Timestamps represent UTC.
- Bars are aligned to one-minute boundaries.
- Records are ordered chronologically.
- Duplicate timestamps are not permitted.
- Timestamp identifies the beginning of the bar interval.

## OHLC rules

For every valid bar:

- high >= open and close.
- low <= open and close.
- high >= low.
- All prices must be finite and positive.

## Volume semantics

Tick count, summed bid volume, summed ask volume, and exchange-traded volume
are not interchangeable. The chosen source measure must be documented and
must not be presented as centralized traded volume unless the source supports
that interpretation.

## Coverage

Annual files must be checked for expected date coverage and missing intervals.
Market closures and source outages must be distinguished from processing
failures.

## Publication requirements

Every published dataset must have:

- Asset and year.
- File path and format.
- Row count and file size.
- SHA-256 checksum.
- First and last timestamps.
- Price basis.
- Explicit volume semantics.
- Successful validation.
