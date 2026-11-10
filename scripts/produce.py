#!/usr/bin/env python3
"""Validate, audit, normalize, and register a Dukascopy M1 parquet export."""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
import numpy as np, pandas as pd, pyarrow.parquet as pq

REQUIRED = ["timestamp", "open", "high", "low", "close", "volume"]
NUMERIC  = ["open", "high", "low", "close", "volume"]
ALIASES  = {"time":"timestamp","datetime":"timestamp","date":"timestamp","date_time":"timestamp","vol":"volume"}
PRICE_BASIS = "Dukascopy BID candles"
VOLUME_TYPE = "Dukascopy provider-reported candle volume; semantics not independently verified"

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

def load_and_normalize(path: Path):
    df = pd.read_parquet(path)
    if isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index()
        for cand in ("index","time","datetime","date"):
            if cand in df.columns and "timestamp" not in df.columns:
                df = df.rename(columns={cand:"timestamp"}); break
    df.columns = [str(c).strip().lower() for c in df.columns]
    for old,new in ALIASES.items():
        if old in df.columns and new not in df.columns: df = df.rename(columns={old:new})
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing: sys.exit(f"MISSING COLUMNS: {missing}. Found: {list(df.columns)}")
    df = df[REQUIRED].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="raise")
    for c in NUMERIC: df[c] = pd.to_numeric(df[c], errors="coerce")
    if df.isnull().any().any():
        sys.exit(f"NULL VALUES:\n{df.isnull().sum().to_string()}")
    return df

def audit_by_trading_day(df, year):
    """Count candles per calendar day; report worst weekdays (not weekends)."""
    df = df.copy()
    df["date"] = df["timestamp"].dt.date
    counts = df.groupby("date").size().rename("candles")
    weekdays = pd.to_datetime(counts.index).weekday  # 0=Mon, 6=Sun
    counts = counts.to_frame()
    counts["weekday"] = weekdays

    # Full weekdays (Mon-Fri) are expected to have near 1440 bars.
    weekday_mask = counts["weekday"] <= 4
    weekday_counts = counts[weekday_mask]
    worst = weekday_counts.nsmallest(15, "candles")

    total_days = len(counts)
    weekday_days = len(weekday_counts)
    low_weekdays = int((weekday_counts["candles"] < 1000).sum())

    print(f"[AUDIT] Calendar days present: {total_days}")
    print(f"[AUDIT] Weekdays (Mon-Fri) present: {weekday_days}")
    print(f"[AUDIT] Weekdays with < 1000 candles: {low_weekdays}")
    print("[AUDIT] 15 sparsest weekdays:")
    for date, row in worst.iterrows():
        print(f"         {date} (wd={int(row['weekday'])}): {int(row['candles'])} candles")

    return weekday_counts, low_weekdays

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    ap.add_argument("--asset", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--min-weekday-candles", type=int, default=1000,
                    help="Fail if any weekday has fewer candles than this")
    ap.add_argument("--max-sparse-weekdays", type=int, default=5,
                    help="Allow this many weekdays below the threshold (holidays)")
    args = ap.parse_args()

    path = Path(args.path)
    print(f"[1] File: {path} ({path.stat().st_size:,} bytes)")
    print(f"[2] Schema: {pq.read_schema(path)}")

    df = load_and_normalize(path)
    print(f"[3] Rows: {len(df):,}  First: {df['timestamp'].iloc[0]}  Last: {df['timestamp'].iloc[-1]}")

    print("[4] Integrity")
    if df["timestamp"].duplicated().any(): sys.exit("DUPLICATE timestamps")
    if not df["timestamp"].is_monotonic_increasing: sys.exit("TIMESTAMPS not increasing")
    o,h,l,c,v = (df[k].to_numpy(float) for k in ("open","high","low","close","volume"))
    if (o<=0).any() or (h<=0).any() or (l<=0).any() or (c<=0).any(): sys.exit("NON-POSITIVE prices")
    if (h<l).any(): sys.exit("HIGH < LOW")
    if (h<np.maximum(o,c)).any(): sys.exit("HIGH < OPEN/CLOSE")
    if (l>np.minimum(o,c)).any(): sys.exit("LOW > OPEN/CLOSE")
    if (v<0).any(): sys.exit("NEGATIVE volume")
    print("    ✅ all integrity checks passed")

    print("[5] Trading-day audit")
    weekday_counts, low_weekdays = audit_by_trading_day(df, args.year)

    if low_weekdays > args.max_sparse_weekdays:
        sys.exit(
            f"TOO MANY SPARSE WEEKDAYS: {low_weekdays} weekdays have < "
            f"{args.min_weekday_candles} candles (allowed: {args.max_sparse_weekdays}). "
            "Investigate above list — these are likely genuine data-loss holes."
        )
    print(f"    ✅ sparse weekdays within tolerance ({low_weekdays} <= {args.max_sparse_weekdays})")

    # Soft global floor (fx markets never reach 525k due to weekends)
    if len(df) < 300_000:
        sys.exit(f"TOO FEW TOTAL ROWS: {len(df):,} < 300,000")

    print("[6] Rewrite + hash")
    temp = path.with_name(path.name + ".tmp")
    try:
        df[REQUIRED].to_parquet(temp, index=False, compression="snappy")
        final_size = temp.stat().st_size; final_hash = sha256(temp)
        temp.replace(path)
    finally:
        if temp.exists(): temp.unlink()

    print("[7] Catalog")
    cat_path = Path("data/index.json")
    cat = json.loads(cat_path.read_text())
    cat["datasets"] = [d for d in cat["datasets"] if not (d["asset"]==args.asset and d["year"]==args.year)]
    cat["datasets"].append({
        "asset": args.asset, "year": args.year, "timeframe": "1m", "format": "parquet",
        "path": args.path, "rows": len(df), "size_bytes": final_size, "sha256": final_hash,
        "first_timestamp": df["timestamp"].iloc[0].isoformat(),
        "last_timestamp":  df["timestamp"].iloc[-1].isoformat(),
        "price_basis": PRICE_BASIS, "volume_type": VOLUME_TYPE,
    })
    cat["datasets"].sort(key=lambda x: (x["asset"], x["year"]))
    cat_path.write_text(json.dumps(cat, indent=2) + "\n")

    print(f"\n✅ DONE: {args.asset} {args.year} — {len(df):,} rows, {final_size:,} bytes")
    print("   Note: rows < 525,600 is expected because Forex does not trade weekends.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
