# Architecture

## Overview

JP OHLCV Library is a static historical-data distribution system.

It does not require a dedicated data API or continuously running server.

## Data source

Historical one-minute BID candles are retrieved from Dukascopy using
`dukascopy-downloader==1.1.0`.

The downloader retrieves native M1 candle data. The production pipeline
does not reconstruct these candles from ticks.

## Data production

Each production job handles exactly one asset and one calendar year.

The pipeline:

1. Restore the instrument/year-specific downloader cache.
2. Retrieve the inclusive annual M1 BID date range.
3. Export the annual candles to Parquet.
4. Normalize the timestamp and numeric columns.
5. Enforce chronological order and unique timestamps.
6. Validate OHLC, volume, timestamps and physical Parquet schema.
7. Calculate row count, file size and SHA-256.
8. Atomically publish the validated Parquet file.
9. Register the verified dataset in `data/index.json`.
10. Commit the dataset and catalog to `develop`.

A failed download or validation must never register a dataset.

## Data distribution

Consumers read the catalog and retrieve referenced Parquet files directly
from GitHub or a compatible CDN.

Applications can cache downloaded datasets.

## Automation

GitHub Actions performs production downloads and validation.

Production jobs are manually dispatched and restricted to `develop`.
Each job handles one asset/year pair.

Downloader caches are preserved between attempts to support recovery.

## Validation

The validation utility is independently usable by developers and consumers.

It checks schema, UTC timestamps, minute alignment, chronological ordering,
duplicate timestamps, numeric values, OHLC consistency and missing-minute gaps.

Missing-minute gaps are reported rather than automatically treated as
corruption because market closures and source coverage must be distinguished
from processing failures.

## Volume semantics

The volume field is retained from Dukascopy's M1 candle export.

Its precise market interpretation is not independently established by this
project. It must not be described as centralized exchange-traded volume.
