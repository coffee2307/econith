# ECONITH — Quant + World Production Runtime

> Cập nhật: 03/07/2026 (UTC+7)  
> Trạng thái: backend ASGI ổn định, dashboard Quant/World hoạt động, cockpit WS live. Đã đóng các khoản nợ kiến trúc trọng yếu:
> **equity sync**, **mode-gated isolation (consumer + producer + CCXT air-gap)**, **Postgres→SQLite failover**, **config centralization**, **API security + audit trail**, và **H200 async training harness**.

ECONITH là hệ thống nghiên cứu định lượng gồm hai miền tách biệt chủ quyền:

- **ECONITH Quant** — ingest dữ liệu thị trường, tạo feature, chạy AI decision, route order intent, kiểm soát rủi ro bằng Sentinel, hiển thị cockpit trading dashboard.
- **ECONITH World** — mô phỏng kinh tế-vĩ mô-địa chính trị bằng sovereign multi-agent graph để stress-test và nghiên cứu hiệu ứng lan truyền.

Vòng đời hoàn chỉnh:

```text
live data -> feature -> signal -> risk gate -> execution/fill -> cockpit telemetry
                 |
                 v
        collect -> label -> train -> verify -> activate -> inference
```

---

## 1. System Runtime Map

```text
main.py (FastAPI ASGI lifespan)
  |
  +-- API Security Layer              core/api/auth.py
  |    +-- APIKeyAuthMiddleware        gate mutating routes (X-API-Key / Bearer)
  |    +-- AuditTrailLogger            rotating JSON audit of operator commands
  |
  +-- Core Engine
  |    +-- EventBus                    core/event_bus.py (pub/sub + mode gate)
  |    +-- TimeEngine                  core/engine.py
  |    +-- 5-phase TickPipeline        SNAPSHOT -> APPLY_EVENTS -> RESOLVE_CONFLICTS -> UPDATE_WORLD -> EMIT_SIGNALS
  |    +-- QuantMode                   core/mode.py (REALITY | SIMULATION)
  |
  +-- Market Data Plane
  |    +-- BinanceWebSocketStreamer    infrastructure/websocket/streamer.py
  |    +-- MarketDataPipeline          infrastructure/preprocessing/pipeline.py
  |    +-- AlternativeDataProvider     infrastructure/alternative/provider.py
  |    +-- MacroIngestionHub           core/ingestion/macro_hub.py
  |
  +-- Quant Brain
  |    +-- Predictor                   ai/inference/predictor.py
  |    +-- Regime + desk fusion         ai/regime/, ai/agents/
  |    +-- AIBridge                    econith_quant/bridge/ai_bridge.py
  |    +-- QuantExecutionBridge        bridges/quant_bridge.py (DOMAIN_QUANT)
  |    +-- CCXTBinanceBridge           quant/ccxt_bridge.py (live/synthetic + air-gap)
  |
  +-- Risk + Persistence
  |    +-- Sentinel                    sentinel/manager.py (execution-truth equity)
  |    +-- CircuitBreaker              sentinel/circuit_breaker.py
  |    +-- StateRecorder               infrastructure/storage/recorder.py
  |    +-- Database failover           config/database.py (Postgres -> SQLite)
  |
  +-- World Simulator
  |    +-- WorldKernel                 ai/simulator_engine/world_kernel.py
  |    +-- SovereignWorldGraph         ai/simulator_engine/sovereign_graph.py
  |    +-- WorldBridge                 bridges/world_bridge.py
  |
  +-- User Interfaces
       +-- MetricsHub                  core/telemetry.py
       +-- CockpitTelemetryHub         core/cockpit/ws.py
       +-- JournalistLLM               ai/journalist/aggregator.py
       +-- Next.js Dashboard           dashboard/
```

Cấu hình tập trung tại `config/settings.py` + `config/environment.py`: `STARTING_CAPITAL`, API auth, audit sink, DB DSN — tất cả bind một lần và chảy xuống mọi subsystem.

---

## 2. Operational Guardrails — cách cô lập REALITY / SIMULATION

`QUANT_MODE` là ranh giới chủ quyền dữ liệu. Việc cách ly được thực thi bằng **bốn tầng phòng vệ độc lập**:

| Tầng | Cơ chế | File |
|---|---|---|
| 1. Consumer gate | `EventBus` drop event `world.*` tới handler `DOMAIN_QUANT` khi `REALITY` | `core/event_bus.py` |
| 2. Producer air-gap | `SovereignWorldGraph` không phát `world.micro_impact` khi không ở SIMULATION | `ai/simulator_engine/sovereign_graph.py` |
| 3. Execution air-gap | `CCXTBinanceBridge` dispose live socket ngay khi rời `REALITY`; chỉ live khi authenticated | `quant/ccxt_bridge.py` |
| 4. Anomaly gate | Inject anomaly bị từ chối ngoài SIMULATION | `main.py`, `core/mode.py` |

### REALITY (mặc định an toàn)

- Quant chỉ ăn dữ liệu thật (Binance WS + alt-data + macro thật).
- Mọi `world.*` hướng vào order-routing bị chặn ở EventBus → không look-ahead bias.
- World simulator vẫn chạy để dashboard hiển thị, nhưng không tác động vào brain.
- CCXT chỉ route live khi có credential thật đã authenticated; nếu không → synthetic fill.

### SIMULATION (sandbox nghiên cứu)

- Cho phép World tác động Quant qua `world.micro_impact` (coupling).
- Cho phép inject anomaly để stress-test.
- CCXT bị air-gap khỏi live socket; fill route sang synthetic.

### Equity truth (Sentinel ↔ Cockpit)

Sentinel subscribe `quant.fill` và replay bằng đúng thuật toán position/PnL của Cockpit. `STARTING_CAPITAL` (mặc định `100000.0`) bind chung cho cả hai → equity của Sentinel và Cockpit Fuel Gauge khớp 1:1. `md.ticker` chỉ dùng mark-to-market + latency heartbeat.

### Database failover

`init_database()` probe primary DSN bằng `SELECT 1` (timeout 5s). Nếu Postgres unreachable → log `CRITICAL` và failover sang `sqlite+aiosqlite:///econith_fallback.db`. Không bao giờ silent-fail.

```text
[DATABASE RUNTIME] Primary Postgres connection failed. Deploying local failover instance.
```

---

## 3. API Security & Audit Trail

Middleware `APIKeyAuthMiddleware` (`core/api/auth.py`) gate các route mutating nhạy cảm:

```text
/api/v1/mode
/api/v1/world/tariff
/api/v1/world/mutate
/api/v1/world/scenario
/api/v1/world/country/{code}/mutate
/api/v1/sentinel/inject
/api/v1/sentinel/reset
/api/v1/time/{speed,pause,resume}
/api/v1/order*
```

Bật auth qua `.env`:

```env
API_AUTH_ENABLED=true
API_KEYS=key_alpha,key_bravo
```

Gọi kèm credential:

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/mode `
  -H "X-API-Key: key_alpha" -H "Content-Type: application/json" `
  -d "{\"mode\":\"SIMULATION\"}"
# hoặc: -H "Authorization: Bearer key_alpha"
```

- Route đọc (GET) và WebSocket stream **không** bị gate → dashboard chạy bình thường.
- Mọi lệnh mutation ghi vào audit trail xoay vòng `logs/econith_audit.log` (JSON lines), key chỉ lưu fingerprint SHA-256, không lưu key thô.
- `API_AUTH_ENABLED=false` (mặc định): route mở nhưng vẫn ghi audit (`ALLOW_AUTH_DISABLED`).

---

## 4. EventBus Topic Contract

| Topic | Producer | Consumer chính |
|---|---|---|
| `md.aggTrade`, `md.depth` | `BinanceWebSocketStreamer` | `MarketDataPipeline` |
| `md.ticker` | `MarketDataPipeline` | `MetricsHub`, `Sentinel`, `CockpitTelemetryHub`, `QuantExecutionBridge` |
| `indicator.obi`, `indicator.volume_delta` | `MarketDataPipeline` | `Predictor`, `MetricsHub` |
| `alt.funding_rate`, `alt.open_interest`, `alt.liquidation` | `AlternativeDataProvider` | `Predictor`, `MetricsHub` |
| `ai.signal` | `Predictor` | `AIBridge`, telemetry |
| `order.intent` | AI/execution bridge | `QuantExecutionBridge` (DOMAIN_QUANT) |
| `quant.fill` | `CCXTBinanceBridge` | `CockpitTelemetryHub`, `Sentinel`, `JournalistLLM` |
| `sentinel.status`, `sentinel.emergency` | `Sentinel` | telemetry, recorder, dashboard |
| `core.macro.context` | `MacroIngestionHub` | cockpit, journalist |
| `world.sovereign`, `world.macro` | World simulator | World dashboard, cockpit macro strip, journalist |
| `world.micro_impact` | `SovereignWorldGraph` | journalist, quant (chỉ SIMULATION) |
| `journalist.news` | `JournalistLLM` | news ticker (đang phát triển) |

---

## 5. Dashboard

Frontend tại `dashboard/` (Next.js / React).

Đã có: landing page animation, Quant Mission Control full-width, right-flank persistent System Event Log, Cockpit HUD (WS `/api/v1/stream/cockpit`) với PnL Altimeter / Margin Fuel Gauge / Flight Log / Allocation Radar, World simulator page.

Endpoint dashboard dùng:

```text
GET  /api/v1/health
GET  /api/v1/metrics
WS   /api/v1/stream/metrics
GET  /api/v1/cockpit/snapshot
WS   /api/v1/stream/cockpit
GET  /api/v1/world/sovereign
GET  /api/v1/world/chronology
GET  /api/v1/macro/snapshot
GET  /api/v1/journalist/news
POST /api/v1/mode                 (protected khi bật auth)
POST /api/v1/world/tariff         (protected)
POST /api/v1/world/country/{code}/mutate  (protected)
```

---

## 6. Cấu trúc thư mục quan trọng

```text
econith/
├── ai/
│   ├── agents/                       # desk logic + model loaders
│   ├── inference/predictor.py        # live boardroom inference
│   ├── journalist/aggregator.py      # event -> financial news synthesis
│   ├── regime/                       # regime classifier/switcher
│   └── simulator_engine/             # World kernel + sovereign graph
├── bridges/
│   ├── quant_bridge.py               # order.intent -> CCXT/synthetic (DOMAIN_QUANT)
│   └── world_bridge.py
├── config/
│   ├── database.py                   # async DB + Postgres->SQLite failover
│   ├── environment.py                # typed env (STARTING_CAPITAL, API auth...)
│   └── settings.py                   # centralized settings surface
├── core/
│   ├── api/auth.py                   # API key/bearer middleware + audit trail
│   ├── cockpit/                      # cockpit schemas + WS router
│   ├── ingestion/                    # macro ingestion hub/adapters
│   ├── engine.py                     # deterministic 5-phase engine
│   ├── event_bus.py                  # pub/sub + mode governance gate
│   ├── mode.py                       # REALITY/SIMULATION singleton
│   └── telemetry.py                  # dashboard read model
├── infrastructure/
│   ├── alternative/  daemon/  preprocessing/  storage/  websocket/
├── quant/
│   ├── ccxt_bridge.py                # live/synthetic execution + air-gap
│   ├── context_slicer.py
│   └── payloads.py
├── sentinel/
│   ├── manager.py                    # execution-truth risk governor
│   ├── circuit_breaker.py
│   └── var.py
├── training/
│   ├── collect.py  label.py  orchestrator.py  deploy.py
│   ├── train_ppo.py  fit_regime.py  train_world.py
│   └── h200/orchestrator.py          # async dataloader + DDP + registry writeout
├── dashboard/
└── main.py
```

---

## 7. Quick-Start Blueprint

### 7.1 Backend — Development

```powershell
cd f:\econith
pip install -r requirements.txt
python main.py
# hoặc: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 7.2 Backend — Production

```bash
# Production: tắt reload, nhiều worker, bật auth
export APP_ENV=production
export API_AUTH_ENABLED=true
export API_KEYS=$(openssl rand -hex 24)
export DATABASE_URL=postgresql://econith:econith@postgres:5432/econith

uvicorn main:app \
  --host 0.0.0.0 --port 8000 \
  --workers 4 \
  --no-server-header \
  --proxy-headers --forwarded-allow-ips="*"
```

> Lưu ý: mỗi uvicorn worker là một process độc lập với EventBus/engine riêng. Với kiến trúc single-engine hiện tại, dùng **1 worker** cho runtime giao dịch stateful; scale bằng nhiều instance sau load balancer chỉ khi đã tách state store dùng chung.

### 7.3 Frontend — Development / Production

```powershell
cd f:\econith\dashboard
npm install
npm run dev          # dev
# production:
npm run build
npm run start
```

Mở: `http://localhost:3000`, `/quant`, `/world`.

### 7.4 Health check

```powershell
curl http://localhost:8000/api/v1/health
```

Kỳ vọng an toàn:

```json
{
  "status": "ok",
  "quant_mode": {
    "mode": "REALITY",
    "coupling_enabled": false,
    "anomaly_injection_enabled": false
  }
}
```

---

## 8. Mode switching & World scenario

```powershell
# Sang sandbox
curl -X POST http://127.0.0.1:8000/api/v1/mode -H "Content-Type: application/json" -d "{\"mode\":\"SIMULATION\"}"
# Về reality
curl -X POST http://127.0.0.1:8000/api/v1/mode -H "Content-Type: application/json" -d "{\"mode\":\"REALITY\"}"

# Tariff shock
curl -X POST http://127.0.0.1:8000/api/v1/world/tariff -H "Content-Type: application/json" -d "{\"source\":\"USA\",\"target\":\"CHN\",\"value\":0.5}"

# Mutate metric quốc gia
curl -X POST http://127.0.0.1:8000/api/v1/world/country/VNM/mutate -H "Content-Type: application/json" -d "{\"group\":\"\",\"field\":\"inflation\",\"value\":0.04}"
```

Khi bật auth, thêm `-H "X-API-Key: <key>"` cho mọi lệnh trên.

---

## 9. Data Acquisition & RunPod H200 Training Blueprint

### Bước 1 — Harvest features (production data)

```powershell
# Thu thập feature live từ Binance
make data-collect
# hoặc backfill lịch sử OHLCV
make data-collect-backfill
```

Output: `datasets/features/features_XXXXX.parquet`.

### Bước 2 — Label

```powershell
make data-label
```

Sinh `datasets/processed/quant_labeled.parquet` + `quant_holdout.parquet` (split theo thời gian 80/20, có cột `reward` anti-greed).

### Bước 3 — Mount dataset lên RunPod H200

```bash
# Trên pod H200
cd /workspace/econith
pip install -r requirements.txt
pip install -r requirements-train.txt   # torch, ray, pyarrow, pyyaml...

# Đưa datasets vào (volume mount / rsync / s3)
ls datasets/features/*.parquet
```

### Bước 4 — Multi-GPU training harness (`training/h200/orchestrator.py`)

Harness gồm:

- **`AsyncTensorLoader`** — async generator stream Parquet/SQLite → tensor batch (đọc IO trong worker thread, overlap compute).
- **`PartitionedTrainer`** — huấn luyện từng `ComponentPartition` cô lập tham số (HRL Meta-Brain, PPO desks, Neural World), mixed precision BF16/FP8, GradScaler version-agnostic.
- **DDP** — tự init `torch.distributed` khi chạy dưới `torchrun` (đọc `WORLD_SIZE`/`RANK`/`LOCAL_RANK`), wrap `DistributedDataParallel`.
- **`RegistryWriter`** — ghi checkpoint + SHA-256 vào `models/registry/manifest.yaml` và promote `active.yaml` khi hoàn tất.

Chạy đơn GPU:

```bash
python -m training.h200.orchestrator
```

Chạy multi-GPU (ví dụ 8× H200) qua torchrun:

```bash
torchrun --standalone --nproc_per_node=8 -m training.h200.orchestrator
```

Trong Python (điều khiển chi tiết):

```python
import asyncio
from training.h200.orchestrator import (
    H200Orchestrator, DataProcessingConfig, ComponentPartition,
)

cfg = DataProcessingConfig(
    parquet_root="datasets/features",
    batch_size=4096,
    feature_columns=(
        "obi", "volume_delta", "buy_volume", "sell_volume", "trade_count",
        "funding_rate", "time_to_funding_s", "open_interest",
        "oi_change_pct", "liquidation_notional",
    ),
    target_column="reward",
)
orch = H200Orchestrator(data_config=cfg)
result = asyncio.run(orch.run_async(
    partitions=[ComponentPartition.PPO_BTC, ComponentPartition.NEURAL_WORLD],
    world_size=8,       # số GPU
    activate=True,      # promote active.yaml sau khi train
))
print(result["partitions"])
```

Theo dõi epoch/step: mỗi 100 step log `loss` qua `MetricSink` (mặc định `ConsoleMetricSink`; có thể inject sink W&B/Prometheus). Env allocator H200 (HBM3e, NCCL NVLink, transformer-engine) tự apply qua `apply_hardware_env()`.

> Không có GPU/torch? Harness tự degrade sang dry-run planner, vẫn ghi manifest/active để CI kiểm chứng.

### Bước 5 — Verify + Deploy an toàn

```powershell
make model-verify     # đối chiếu SHA-256 từng checkpoint theo manifest
make model-deploy     # activate.yaml + archive history rollback
```

Rollback:

```powershell
python training/deploy.py --rollback
```

### Bước 6 — Restart backend nạp model mới

```powershell
python main.py
```

Sau restart, `ai.signal` sẽ có `agent_brain=trained` / `regime_brain=trained` khi checkpoint hợp lệ.

---

## 10. Roadmap tiếp theo

### P0 — Stabilization
1. Test suite chính thức cho: equity sync, mode-gate, DB failover, auth middleware.
2. Tách producer theo mode triệt để hơn (throttle stream thừa).

### P1 — Production readiness
3. Observability: JSON logs, Prometheus metrics (tick latency, WS reconnect, fills, freeze, DB failover), Grafana.
4. Rate limit + command guard trên các route mutating.
5. News ticker UI đọc `journalist.news`.
6. LLM backend thật cho Journalist (OpenAI/Anthropic/Ollama qua env).

### P2 — Quant capability
7. Multi-symbol portfolio + portfolio-level VaR.
8. Backtesting harness (SIMULATION + historical feed) với report hit-rate/drawdown/turnover.
9. Paper trading campaign trên testnet đủ dài.
10. Model governance: commit hash + dataset hash + metrics trong manifest.

### P3 — Scaling
11. H200 pipeline nối dataset registry + checkpoint store thật.
12. VPS daemon deploy (systemd/Docker service).
13. Docker compose production: backend + dashboard + Postgres + Redis + Prometheus + Grafana.

---

## 11. Security rules

- Không commit `.env`.
- Không chụp/chia sẻ ảnh chứa API key/secret.
- Bật `API_AUTH_ENABLED=true` + `API_KEYS` mạnh trước khi expose backend.
- Dùng Binance testnet trước khi bật trade credential thật.
- Nếu nghi lộ key: revoke ngay, tạo key mới, restart backend.
- Không expose port backend public nếu chưa có auth + TLS.

---

## 12. Engineering state hiện tại

- Runtime event-driven đầy đủ, deterministic 5-phase tick.
- Quant/World mode sovereignty với 4 tầng cô lập.
- Sentinel dùng execution-truth equity, khớp Cockpit 1:1.
- CCXT live/synthetic execution có air-gap.
- Database failover thay vì silent fail.
- Config tập trung + API security + audit trail.
- H200 async training harness DDP-ready, ghi registry tự động.

Việc tiếp theo: **đóng test, quan sát hệ thống, rồi mở rộng multi-symbol và paper campaign** — không phải thêm màn hình.
