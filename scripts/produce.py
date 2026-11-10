#!/usr/bin/env python3
"""Validate, normalize, and register a Dukascopy M1 parquet export."""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
import numpy as np, pandas as pd, pyarrow.parquet as pq

REQUIRED = ["timestamp", "open", "high", "low", "close", "volume"]
NUMERIC  = ["open", "high", "low", "close", "volume"]
ALIASES  = {"time":"timestamp","datetime":"timestamp","date":"timestamp","date_time":"timestamp","vol":"volume"}
MIN_ROWS = {"EURUSD":480_000,"GBPUSD":480_000,"USDJPY":480_000,"XAUUSD":480_000,"US30":100_000,"US100":100_000}
PRICE_BASIS = "Dukascopy BID candles"
VOLUME_TYPE = "Dukascopy provider-reported candle volume; semantics not independently verified"

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    ap.add_argument("--asset", required=True)
    ap.add_argument("--year", type=int, required=True)
    args = ap.parse_args()

    path = Path(args.path)
    print(f"[1] File: {path}")
    if not path.exists(): sys.exit(f"MISSING FILE: {path}")
    print(f"    Size: {path.stat().st_size:,} bytes")

    schema = pq.read_schema(path)
    print(f"[2] Schema: {schema}")
    print(f"    Columns: {schema.names}")

    df = pd.read_parquet(path)
    print(f"[3] Shape: {df.shape}")
    print(f"    Dtypes:\n{df.dtypes.to_string()}")
    print(f"    Index: {type(df.index).__name__} name={df.index.name!r}")

    if isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index()
        for cand in ("index", "time", "datetime", "date"):
            if cand in df.columns and "timestamp" not in df.columns:
                df = df.rename(columns={cand: "timestamp"}); break
        print(f"[4] After reset: {list(df.columns)}")

    df.columns = [str(c).strip().lower() for c in df.columns]
    for old, new in ALIASES.items():
        if old in df.columns and new not in df.columns:
            df = df.rename(columns={old: new})
    print(f"[5] Columns: {list(df.columns)}")

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing: sys.exit(f"MISSING COLUMNS: {missing}. Found: {list(df.columns)}")

    df = df[REQUIRED].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="raise")
    for c in NUMERIC: df[c] = pd.to_numeric(df[c], errors="coerce")

    nulls = df.isnull().sum()
    print(f"[6] Nulls:\n{nulls.to_string()}")
    if (nulls > 0).any():
        print(df[df.isnull().any(axis=1)].head().to_string())
        sys.exit(f"NULL values in {nulls[nulls>0].to_dict()}")

    print("[7] Integrity")
    if bool(df["timestamp"].duplicated().any()):
        sys.exit(f"DUPLICATE timestamps: {int(df['timestamp'].duplicated().sum())}")
    if not bool(df["timestamp"].is_monotonic_increasing):
        sys.exit("TIMESTAMPS not strictly increasing")

    o,h,l,c,v = (df[k].to_numpy(dtype=float) for k in ("open","high","low","close","volume"))
    print(f"    O: [{o.min():.5f},{o.max():.5f}]  H: [{h.min():.5f},{h.max():.5f}]")
    print(f"    L: [{l.min():.5f},{l.max():.5f}]  C: [{c.min():.5f},{c.max():.5f}]")
    print(f"    V: [{v.min():.2f},{v.max():.2f}]")

    if (o<=0).any() or (h<=0).any() or (l<=0).any() or (c<=0).any(): sys.exit("NON-POSITIVE prices")
    if (h<l).any():                     sys.exit("HIGH < LOW")
    if (h<np.maximum(o,c)).any():       sys.exit("HIGH < OPEN/CLOSE")
    if (l>np.minimum(o,c)).any():       sys.exit("LOW > OPEN/CLOSE")
    if (v<0).any():                     sys.exit("NEGATIVE volume")
    if not np.isfinite(np.concatenate([o,h,l,c])).all(): sys.exit("NON-FINITE prices")

    print("[8] Coverage")
    total = len(df)
    first = df["timestamp"].iloc[0]; last = df["timestamp"].iloc[-1]
    start = pd.Timestamp(f"{args.year}-01-01", tz="UTC")
    end   = pd.Timestamp(f"{args.year+1}-01-01", tz="UTC")
    expected = int((end - start).total_seconds() / 60)
    coverage = total / expected
    print(f"    Rows: {total:,} / {expected:,} expected ({coverage:.2%})")
    print(f"    First: {first}  Last: {last}")
    min_rows = MIN_ROWS.get(args.asset, 100_000)
    if total < min_rows:
        sys.exit(f"INCOMPLETE: {total:,} rows < required {min_rows:,} for {args.asset}")
    print(f"    ✅ row threshold met ({min_rows:,})")

    print("[9] Rewrite + hash")
    temp = path.with_name(path.name + ".tmp")
    try:
        df.to_parquet(temp, index=False, compression="snappy")
        print(f"    New schema: {pq.read_schema(temp)}")
        final_size = temp.stat().st_size
        final_hash = sha256(temp)
        temp.replace(path)
    finally:
        if temp.exists(): temp.unlink()
    print(f"    Size: {final_size:,}  SHA256: {final_hash[:16]}...")

    print("[10] Catalog")
    cat_path = Path("data/index.json")
    cat = json.loads(cat_path.read_text(encoding="utf-8"))
    cat["datasets"] = [d for d in cat["datasets"] if not (d["asset"]==args.asset and d["year"]==args.year)]
    cat["datasets"].append({
        "asset": args.asset, "year": args.year,
        "timeframe": "1m", "format": "parquet", "path": args.path,
        "rows": total, "size_bytes": final_size, "sha256": final_hash,
        "first_timestamp": first.isoformat(), "last_timestamp": last.isoformat(),
        "price_basis": PRICE_BASIS, "volume_type": VOLUME_TYPE,
    })
    cat["datasets"].sort(key=lambda x: (x["asset"], x["year"]))
    cat_path.write_text(json.dumps(cat, indent=2) + "\n", encoding="utf-8")
    print(f"    Catalog now has {len(cat['datasets'])} datasets")

    print(f"\n✅ DONE: {args.asset} {args.year} — {total:,} rows, {final_size:,} bytes, {coverage:.2%}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
