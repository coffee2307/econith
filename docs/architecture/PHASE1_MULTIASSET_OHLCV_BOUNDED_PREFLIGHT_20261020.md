# ECONITH Phase 1 — Multi-asset Spot OHLCV bounded preflight (2026-10-20)

**Scope:** research-only, opt-in public data-only REST, SIMULATION only, no order APIs, no main.py runtime changes, no 24h feed, no verified listing/delisting history. **P1-07 remains 0/10, Phase 1 remains 81/100 provisional until actual independent acceptance.** Do not upload raw public market payloads to GitHub.

## Supported preflight

- Three independent Spot pairs: BTCUSDT, ETHUSDT, BNBUSDT; timeframes 1m and 5m; 100 bars each = 600 bounded bars. Exactly 2 public clock GETs, 3 single-symbol exchangeInfo GETs and 6 klines GETs. No retries on rate limits, no key and no trading methods.
- Binance public data-only HTTPS base: https://data-api.binance.vision. Endpoints allowlisted to /api/v3/time, /api/v3/exchangeInfo and /api/v3/klines. Current symbol status is a snapshot, not verified historical listing/delisting.
- Reject duplicate, gapped, inverted, unaligned and missing final closed candles; incomplete/future bars, NaN, negative volumes and invalid price geometry; mismatched symbols or clock, malformed responses and tampering.
- As-of cutoff is derived from serverTime BEFORE public requests. Source response bytes stay in a private fresh absolute output directory, with separate SHA-256 ledger and independent read-only replay. Max 2MB per HTTP response.

## Offline deterministic tests (no network)

```bash
python -m unittest -v tests.test_phase1_spot_multiasset_ohlcv
```

## Finite public research pilot (manual opt-in ONLY)

```bash
python -m ci.phase1_spot_multiasset_ohlcv --allow-public-market-data --acknowledge-finite-not-coverage --output-dir /ABSOLUTE/PRIVATE/FRESH/DIR
python -m ci.phase1_spot_multiasset_ohlcv_audit --root /ABSOLUTE/PRIVATE/FRESH/DIR
```

Outputs include private original HTTP JSON and aggregate_report.json. Never publish the full output directory. The audit performs local digest, time-as-of, price/volume and summary checks: it does not independently retrieve exchange data, cryptographically attest Binance, or establish historical completeness.

**Institutional debt:** independently reconstruct a subset of candles from timestamped raw trades; verify delisting/onboarding separately; obtain uninterrupted source service/retained raw data and operator-approved 24–72h run. Frozen FIFO 128, freshness 5s, failure quarantine and zero orders remain unchanged. The workflow added by this change runs offline only.
