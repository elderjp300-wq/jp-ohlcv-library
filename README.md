# JP OHLCV Library

A public repository of historical **1-minute OHLCV** datasets for quantitative
research, backtesting and market analysis.

- **6 instruments** × **3 years** = **18 datasets**, all M1, UTC, Parquet
- Data source: **Dukascopy** BID candles
- Every file is validated, checksummed (SHA-256), and registered in
  [`data/index.json`](data/index.json)

## Coverage

| Instrument | 2023 | 2024 | 2025 | Approx. rows / year |
|---|:---:|:---:|:---:|---:|
| EURUSD | ✓ | ✓ | ✓ | ~371,000 |
| GBPUSD | ✓ | ✓ | ✓ | ~371,000 |
| USDJPY | ✓ | ✓ | ✓ | ~372,000 |
| XAUUSD | ✓ | ✓ | ✓ | ~354,000 |
| US30   | ✓ | ✓ | ✓ | ~341,000 |
| US100  | ✓ | ✓ | ✓ | ~341,000 |

Row counts below 525,600 are expected: Forex and index CFDs do not trade 24/7
(weekends, and for indices, restricted sessions).

## Access

**Stable (recommended)** — pinned to tag `v1.0.0`:

```
https://raw.githubusercontent.com/elderjp300-wq/jp-ohlcv-library/v1.0.0/data/ASSET/YEAR/1min.parquet
```

**Latest on `main`**:

```
https://raw.githubusercontent.com/elderjp300-wq/jp-ohlcv-library/main/data/ASSET/YEAR/1min.parquet
```

**Catalog (JSON)**:

```
https://raw.githubusercontent.com/elderjp300-wq/jp-ohlcv-library/main/data/index.json
```

Examples:

```
https://raw.githubusercontent.com/elderjp300-wq/jp-ohlcv-library/v1.0.0/data/EURUSD/2023/1min.parquet
https://raw.githubusercontent.com/elderjp300-wq/jp-ohlcv-library/v1.0.0/data/XAUUSD/2024/1min.parquet
```

## Schema

Every file has exactly these columns:

| Column | Type | Description |
|---|---|---|
| `timestamp` | `timestamp[us, UTC]` | Opening time of the 1-minute bar |
| `open` | `float64` | First price in the interval |
| `high` | `float64` | Highest price in the interval |
| `low` | `float64` | Lowest price in the interval |
| `close` | `float64` | Last price in the interval |
| `volume` | `float64` | Dukascopy provider-reported candle volume |

- Timestamps are UTC, minute-aligned, strictly increasing, no duplicates
- Prices are positive and internally consistent (`high ≥ max(open, close)`, etc.)
- Compression: Snappy

### Volume

The `volume` field is **Dukascopy's provider-reported candle volume**. Its exact
market interpretation is not independently established and **must not** be
presented as centralized exchange-traded volume.

### Price basis

BID side.

## Verification

Each entry in `data/index.json` includes `rows`, `size_bytes`, `sha256`,
`first_timestamp`, `last_timestamp`. Verify any file against its catalog
`sha256`:

```bash
URL="https://raw.githubusercontent.com/elderjp300-wq/jp-ohlcv-library/v1.0.0/data/EURUSD/2023/1min.parquet"
curl -sL "$URL" -o eurusd_2023.parquet
sha256sum eurusd_2023.parquet
# compare to the corresponding sha256 in data/index.json
```

## Using it in a web app

```js
const BASE = "https://raw.githubusercontent.com/elderjp300-wq/jp-ohlcv-library/v1.0.0";

const catalog = await fetch(`${BASE}/data/index.json`).then(r => r.json());

const entry = catalog.datasets.find(
  d => d.asset === "EURUSD" && d.year === 2023
);

const buf = await fetch(`${BASE}/${entry.path}`).then(r => r.arrayBuffer());

// Parse in the browser with hyparquet or duckdb-wasm
```

## Reproducing / extending

Datasets are produced by a manually dispatched GitHub Actions workflow:

- `.github/workflows/produce-annual.yml`
- Uses `dukascopy-downloader==1.1.0` (native M1 candles, no tick resampling)
- Validates each file before committing
- Only commits when validation passes

To add a dataset: **Actions → Produce Annual Dataset → Run workflow**.

## Branches and tags

- `develop` — active development, all production runs push here first
- `main` — stable mirror of `develop`, updated on every successful release
- `v1.0.0` — first stable data release (all 18 datasets)

## License

No open-source license has been granted. All rights reserved unless explicitly
stated otherwise. See `pyproject.toml` (`license = { text = "UNLICENSED" }`).
