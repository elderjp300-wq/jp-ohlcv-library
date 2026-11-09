# Architecture

## Overview

JP OHLCV Library is a static historical-data distribution system.
It does not require a dedicated data API or continuously running server.

## Data production

The intended pipeline:

1. Retrieve historical source data.
2. Decode and normalize source records.
3. Aggregate records into UTC 1-minute OHLCV bars.
4. Process bounded chunks to control memory usage.
5. Validate the resulting dataset.
6. Write a Parquet file.
7. Calculate its SHA-256 checksum.
8. Register the verified dataset in `data/index.json`.

A dataset must not be registered before validation succeeds.

## Data distribution

Consumers read the catalog and retrieve referenced Parquet files directly
from GitHub or a compatible CDN. Applications can cache downloaded datasets.

## Automation

GitHub Actions will handle scheduled data updates, processing, validation,
and catalog updates. Automation must never publish incomplete or invalid data.

## Validation

The validation utility will be independently usable by developers and
consumers. It will inspect downloaded datasets without requiring a connection
to a JP OHLCV data server.
