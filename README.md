# OANDA Forex Trading System

Research-to-production systematic FX trading pipeline using OANDA practice → live.

## Setup

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dependencies and the package in editable mode
pip install -r requirements.txt
pip install -e .

# 3. Configure credentials
cp .env.example .env
# Edit .env with your OANDA practice API token and account ID

# 4. Run the setup notebook to verify connectivity
jupyter notebook notebooks/00_setup.ipynb
```

## Workflow

1. **Data pull** — `notebooks/01_data_pull.ipynb` — fetch and cache OHLCV candles
2. **Features** — `notebooks/02_feature_pipeline.ipynb` — build feature matrix
3. **Baselines** — `notebooks/03_strategy_baselines.ipynb` — rule-based strategy benchmarks
4. **ML training** — `notebooks/04_ml_training.ipynb` — walk-forward model training
5. **Backtest** — `notebooks/05_walk_forward_backtest.ipynb` — full evaluation + tear sheet

## Streamlit App

```bash
streamlit run app/streamlit_app.py
```

## Tests

```bash
pytest tests/ -v
```

## Project Structure

```
src/forex_system/   Python package (shared library)
notebooks/          Research and validation notebooks
app/                Streamlit operations interface
data/
  raw/              Cached OANDA candle pulls (parquet)
  processed/        Feature matrices (parquet)
  artifacts/        Trained model files (pkl)
  reports/          Backtest tear sheets, run logs
tests/              Unit tests
```

## Disclaimer

For research and educational purposes only. FX trading on margin involves substantial risk.
