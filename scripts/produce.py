#!/usr/bin/env python3
"""Validate, audit, normalize, register a Dukascopy M1 parquet export."""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
import numpy as np, pandas as pd, pyarrow.parquet as pq

REQUIRED = ["timestamp","open","high","low","close","volume"]
NUMERIC  = ["open","high","low","close","volume"]
ALIASES  = {"time":"timestamp","datetime":"timestamp","date":"timestamp","date_time":"timestamp","vol":"volume"}
PRICE_BASIS = "Dukascopy BID candles"
VOLUME_TYPE = "Dukascopy provider-reported candle volume; semantics not independently verified"

def thresholds(asset: str) -> dict:
    # Index CFDs trade ~6.5-10h/day, forex ~24h x 5 days/week
    if asset in {"US30","US100"}:
        return {"min_candles": 200, "max_sparse": 20, "min_total": 40_000}
    return {"min_candles": 1000, "max_sparse": 5, "min_total": 300_000}

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""): h.update(c)
    return h.hexdigest()

def normalize(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index()
        for c in ("index","time","datetime","date"):
            if c in df.columns and "timestamp" not in df.columns:
                df = df.rename(columns={c:"timestamp"}); break
    df.columns = [str(c).strip().lower() for c in df.columns]
    for o,n in ALIASES.items():
        if o in df.columns and n not in df.columns: df = df.rename(columns={o:n})
    miss = [c for c in REQUIRED if c not in df.columns]
    if miss: sys.exit(f"MISSING COLUMNS: {miss}. Found: {list(df.columns)}")
    df = df[REQUIRED].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="raise")
    for c in NUMERIC: df[c] = pd.to_numeric(df[c], errors="coerce")
    if df.isnull().any().any(): sys.exit(f"NULL VALUES:\n{df.isnull().sum().to_string()}")
    return df

def audit(df: pd.DataFrame, min_candles: int) -> int:
    df = df.copy(); df["date"] = df["timestamp"].dt.date
    counts = df.groupby("date").size().rename("candles").to_frame()
    counts["wd"] = pd.to_datetime(counts.index).weekday
    wk = counts[counts["wd"] <= 4]
    print(f"[AUDIT] calendar days={len(counts)}  weekdays={len(wk)}")
    print(f"[AUDIT] 15 sparsest weekdays (threshold={min_candles}):")
    for d,r in wk.nsmallest(15,"candles").iterrows():
        print(f"         {d} (wd={int(r['wd'])}): {int(r['candles'])} candles")
    return int((wk["candles"] < min_candles).sum())

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    ap.add_argument("--asset", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--skip-rewrite", action="store_true",
                    help="Do not rewrite parquet (used by publish-retry)")
    a = ap.parse_args()

    t = thresholds(a.asset)
    path = Path(a.path)
    print(f"[1] {path} ({path.stat().st_size:,} bytes)")
    df = normalize(path)
    print(f"[3] rows={len(df):,}  first={df['timestamp'].iloc[0]}  last={df['timestamp'].iloc[-1]}")

    if df["timestamp"].duplicated().any(): sys.exit("DUPLICATE timestamps")
    if not df["timestamp"].is_monotonic_increasing: sys.exit("TIMESTAMPS not increasing")
    o,h,l,c,v = (df[k].to_numpy(float) for k in ("open","high","low","close","volume"))
    if (o<=0).any() or (h<=0).any() or (l<=0).any() or (c<=0).any(): sys.exit("NON-POSITIVE prices")
    if (h<l).any(): sys.exit("HIGH < LOW")
    if (h<np.maximum(o,c)).any(): sys.exit("HIGH < OPEN/CLOSE")
    if (l>np.minimum(o,c)).any(): sys.exit("LOW > OPEN/CLOSE")
    if (v<0).any(): sys.exit("NEGATIVE volume")
    print("[4] integrity OK")

    print("[5] audit")
    sparse = audit(df, t["min_candles"])
    if sparse > t["max_sparse"]:
        sys.exit(f"TOO MANY SPARSE WEEKDAYS: {sparse} > {t['max_sparse']}")
    if len(df) < t["min_total"]:
        sys.exit(f"TOO FEW ROWS: {len(df):,} < {t['min_total']:,}")
    print(f"    ✅ sparse={sparse} ≤ {t['max_sparse']}, total={len(df):,} ≥ {t['min_total']:,}")

    if not a.skip_rewrite:
        tmp = path.with_name(path.name + ".tmp")
        try:
            df[REQUIRED].to_parquet(tmp, index=False, compression="snappy")
            tmp.replace(path)
        finally:
            if tmp.exists(): tmp.unlink()
        print("[6] parquet rewritten")
    else:
        print("[6] parquet NOT rewritten (skip-rewrite)")

    size = path.stat().st_size
    h256 = sha256(path)

    cat_path = Path("data/index.json")
    cat = json.loads(cat_path.read_text())
    cat["datasets"] = [d for d in cat["datasets"]
                       if not (d["asset"]==a.asset and d["year"]==a.year)]
    cat["datasets"].append({
        "asset": a.asset, "year": a.year, "timeframe": "1m", "format": "parquet",
        "path": a.path, "rows": len(df), "size_bytes": size, "sha256": h256,
        "first_timestamp": df["timestamp"].iloc[0].isoformat(),
        "last_timestamp":  df["timestamp"].iloc[-1].isoformat(),
        "price_basis": PRICE_BASIS, "volume_type": VOLUME_TYPE,
    })
    cat["datasets"].sort(key=lambda x: (x["asset"], x["year"]))
    cat_path.write_text(json.dumps(cat, indent=2) + "\n")
    print(f"[7] catalog now has {len(cat['datasets'])} datasets")
    print(f"\n✅ DONE: {a.asset} {a.year} — {len(df):,} rows")
    return 0

if __name__ == "__main__":
    sys.exit(main())
