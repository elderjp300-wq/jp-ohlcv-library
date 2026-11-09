import os
import sys
import json
import struct
import lzma
import datetime
import requests
import pandas as pd

# Base public data URL for Dukascopy binary feeds
DUKAS_URL = "https://dukascopy.com{asset}/{year}/{month:02d}/{day:02d}/{hour:02d}h_ticks.bi5"

def download_day_data(asset, target_date):
    """Downloads and unpacks 24 hours of binary tick data from Dukascopy."""
    day_ticks = []
    
    # Dukascopy URLs require lowercase pair names (e.g., eurusd)
    url_asset = asset.lower()
    
    for hour in range(24):
        url = DUKAS_URL.format(
            asset=url_asset, 
            year=target_date.year, 
            month=target_date.month - 1, # 0-indexed months
            day=target_date.day, 
            hour=hour
        )
        
        try:
            res = requests.get(url, timeout=10)
            if res.status_code != 200:
                continue 
                
            raw_bytes = lzma.decompress(res.content)
            struct_len = 20
            for i in range(0, len(raw_bytes), struct_len):
                chunk = raw_bytes[i:i+struct_len]
                if len(chunk) < struct_len:
                    continue
                
                time_offset, ask, bid, ask_vol, bid_vol = struct.unpack("!IIIff", chunk)
                
                base_hour = datetime.datetime(target_date.year, target_date.month, target_date.day, hour, tzinfo=datetime.timezone.utc)
                tick_time = base_hour + datetime.timedelta(milliseconds=time_offset)
                
                point_multiplier = 1000 if "JPY" in asset or asset in ["XAUUSD", "USDCAD"] else 100000
                
                day_ticks.append({
                    "timestamp": tick_time,
                    "price": bid / point_multiplier
                })
        except Exception:
            continue
            
    return day_ticks

def process_year(asset, year):
    """Loops through a whole calendar year, samples to 1min, and stores dataset."""
    print(f"🚀 Processing 1min OHLCV data library generation for {asset} - {year}...")
    
    start_date = datetime.date(year, 1, 1)
    end_date = datetime.date(year, 12, 31)
    current_date = start_date
    
    all_ticks = []
    
    while current_date <= end_date:
        sys.stdout.write(f"\rDownloading Date: {current_date.strftime('%Y-%m-%d')} | Total Ticks Picked Up: {len(all_ticks)}")
        sys.stdout.flush()
        
        day_data = download_day_data(asset, current_date)
        all_ticks.extend(day_data)
        current_date += datetime.timedelta(days=1)
        
    if not all_ticks:
        print("\n❌ Failed: No data logs found. Verify asset code syntax.")
        return

    df = pd.DataFrame(all_ticks)
    df.set_index("timestamp", inplace=True)
    
    print("\nFormatting data to 1min bars...")
    ohlcv = df["price"].resample("1min").ohlc()
    ohlcv["volume"] = df["price"].resample("1min").count()
    ohlcv.dropna(inplace=True) 
    
    output_dir = f"data/{asset}/{year}"
    os.makedirs(output_dir, exist_ok=True)
    file_path = f"{output_dir}/1min.csv.gz"
    
    ohlcv.to_csv(file_path, compression="gzip")
    print(f"✅ Data saved successfully to: {file_path}")
    
    update_index(asset, year)

def update_index(asset, year):
    index_path = "data/index.json"
    with open(index_path, "r") as f:
        data_tree = json.load(f)
        
    if asset not in data_tree:
        data_tree[asset] = []
    if year not in data_tree[asset]:
        data_tree[asset].append(year)
        
    with open(index_path, "w") as f:
        json.dump(data_tree, f, indent=4)

if __name__ == "__main__":
    target_asset = "EURUSD"
    target_year = 2024
    process_year(target_asset, target_year)
