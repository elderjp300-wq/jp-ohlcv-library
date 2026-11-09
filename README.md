# JP OHLCV Library

A public repository of historical one-minute OHLCV datasets for quantitative research, backtesting and market analysis.

## Supported instruments

- EURUSD
- GBPUSD
- US30
- US100
- XAUUSD
- USDJPY

## Dataset coverage

The initial publication targets 2023, 2024 and 2025.

Each published dataset will use this structure:

`data/ASSET/YEAR/1min.parquet`

Only datasets that have been generated and passed validation will appear in `data/index.json`.

## Data format

- Timeframe: 1 minute
- Timestamp timezone: UTC
- Format: Apache Parquet
- Price basis and volume semantics: documented in the dataset catalog
- Source: Dukascopy historical market data

Dukascopy tick volume fields must not be confused with exchange-traded volume. The published volume definition will be documented explicitly.

## Accessing the data

Datasets are distributed as static files through GitHub. Applications can read the catalog at:

`https://raw.githubusercontent.com/elderjp300-wq/jp-ohlcv-library/develop/data/index.json`

Published dataset files can be downloaded directly from their repository paths.

## Validation

The project includes a standalone Parquet validation tool. It checks schema, timestamps, OHLC integrity, volume values and file integrity.

## Processing

Data processing, validation and publication are designed to run through GitHub Actions on remote Linux runners. Local compilation and Parquet dependencies are not required.

## Development

- `develop`: active development and dataset preparation
- `main`: production branch; kept untouched during development

## License

No open-source license has been granted. All rights are reserved unless explicitly stated otherwise.
