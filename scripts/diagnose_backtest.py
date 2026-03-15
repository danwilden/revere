import duckdb
import json

DB_PATH = "data/market.duckdb"
INSTRUMENT = "EUR_USD"
TIMEFRAME = "H1"
MODEL_ID = "32b8bd00-22a3-4ff6-8dd0-1abc2c63c2bb"
FEATURE_RUN_ID = "84e2fb26-ef9c-4d21-bd36-d1c9406db493"
START = "2022-01-02"
END = "2023-12-31"

con = duckdb.connect(DB_PATH, read_only=True)

# 1. Regime label coverage
print("=== Regime Label Coverage ===")
r = con.execute("""
    SELECT regime_label, COUNT(*) as cnt
    FROM regime_labels
    WHERE model_id = ? AND instrument_id = ? AND timeframe = ?
      AND timestamp_utc >= ? AND timestamp_utc < ?
    GROUP BY regime_label ORDER BY cnt DESC
""", [MODEL_ID, INSTRUMENT, TIMEFRAME, START, END]).fetchall()
print(r or "NO ROWS FOUND")

print("\n=== Total Regime Label Rows (any date) ===")
r2 = con.execute("""
    SELECT COUNT(*) as total
    FROM regime_labels
    WHERE model_id = ? AND instrument_id = ? AND timeframe = ?
""", [MODEL_ID, INSTRUMENT, TIMEFRAME]).fetchone()
print(r2)

# 2. Feature coverage
print("\n=== Feature Coverage ===")
r = con.execute("""
    SELECT feature_name, COUNT(*) as cnt,
           SUM(CASE WHEN feature_value > 0 THEN 1 ELSE 0 END) as positive_count
    FROM features
    WHERE feature_run_id = ? AND instrument_id = ? AND timeframe = ?
      AND timestamp_utc >= ? AND timestamp_utc < ?
    GROUP BY feature_name ORDER BY feature_name
""", [FEATURE_RUN_ID, INSTRUMENT, TIMEFRAME, START, END]).fetchall()
print(r or "NO ROWS FOUND")

# 3. Check what's actually in regime_labels table
print("\n=== All regime_labels rows (any model, limited to 5) ===")
r = con.execute("""
    SELECT model_id, instrument_id, timeframe, COUNT(*) as cnt
    FROM regime_labels
    GROUP BY model_id, instrument_id, timeframe
    ORDER BY cnt DESC
    LIMIT 10
""").fetchall()
print(r or "NO ROWS IN TABLE")

# 4. Sample bars with regime labels and key features
print("\n=== Sample Bars ===")
r = con.execute("""
    SELECT b.timestamp_utc, rl.regime_label,
           MAX(CASE WHEN f.feature_name='breakout_20' THEN f.feature_value END) as breakout_20,
           MAX(CASE WHEN f.feature_name='adx_14' THEN f.feature_value END) as adx_14,
           MAX(CASE WHEN f.feature_name='ema_slope_20' THEN f.feature_value END) as ema_slope_20
    FROM bars_agg b
    LEFT JOIN regime_labels rl
        ON rl.instrument_id = b.instrument_id AND rl.timeframe = b.timeframe
        AND rl.timestamp_utc = b.timestamp_utc AND rl.model_id = ?
    LEFT JOIN features f
        ON f.instrument_id = b.instrument_id AND f.timeframe = b.timeframe
        AND f.timestamp_utc = b.timestamp_utc AND f.feature_run_id = ?
    WHERE b.instrument_id = ? AND b.timeframe = ?
      AND b.timestamp_utc >= ? AND b.timestamp_utc < ?
    GROUP BY b.timestamp_utc, rl.regime_label
    ORDER BY b.timestamp_utc LIMIT 5
""", [MODEL_ID, FEATURE_RUN_ID, INSTRUMENT, TIMEFRAME, START, END]).fetchall()
print(r)

# 5. Check how many bars_agg rows exist for the period
print("\n=== Bars in period ===")
r = con.execute("""
    SELECT COUNT(*) FROM bars_agg
    WHERE instrument_id = ? AND timeframe = ?
      AND timestamp_utc >= ? AND timestamp_utc < ?
""", [INSTRUMENT, TIMEFRAME, START, END]).fetchone()
print(r)

# 6. Check signals table for any signals linked to this model
print("\n=== Signals metadata (checking signals.json) ===")
try:
    with open("data/metadata/signals.json") as f:
        signals = json.load(f)
    for s in signals:
        if s.get("model_id") == MODEL_ID:
            print(json.dumps(s, indent=2))
except FileNotFoundError:
    print("signals.json not found")

con.close()
