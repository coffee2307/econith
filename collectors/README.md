# ECONITH :: collectors (standalone data-collection unit)

This folder is a **self-contained, zero-ML deployment unit**. Copy *only* this
folder to a remote VPS to run 24/7 data collection without installing PyTorch,
Ray, or any of the main platform's heavy dependencies.

## Boundary rule

`collectors/` imports **only** lightweight libs: `polars`/`pandas`,
`pyarrow`, `websockets`, `httpx`, and the Python stdlib. It never imports
`ai/`, `training/`, `torch`, or the live FastAPI runtime.

## Layout

```text
collectors/
├── requirements.txt        # the ONLY deps needed on the VPS
├── shared/
│   ├── schemas.py          # CrossAssetTick + validation
│   ├── partitioning.py     # raw/<class>/<desk>/<symbol>/<date> paths
│   └── persistence.py      # non-blocking Polars Parquet snapshot writer
├── market_coin/            # 24/7 HF crypto tick/orderbook daemon
├── macro_global/           # scheduled macro pulls + snapshots
└── tradfi_assets/          # session-based tradfi polling
```

## Deploy to a VPS

```bash
# on your machine
scp -r collectors/ user@vps:/opt/econith-collectors/

# on the VPS
cd /opt/econith-collectors
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# run the crypto collector (systemd/tmux for 24/7)
python -m collectors.market_coin.daemon
```

## Output

Everything lands in a partitioned raw lake:

```text
datasets/raw/market/crypto_majors/BTCUSDT/2026-07-04/aggTrade_14__00000.parquet
datasets/raw/macro/series/FEDFUNDS/2026-07/cpi_00__00000.parquet
datasets/raw/tradfi/commodities/GOLD/2026-07-04/spot_14__00000.parquet
```

Rsync `datasets/raw/` back to the training host (or an object store) and the
`training/` tier refines it into the cross-asset feature store.
