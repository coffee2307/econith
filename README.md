# ECONITH — Quant + World Production Runtime

> Cập nhật: 04/07/2026 (UTC+7)  
> Trạng thái: nền runtime đã đủ ổn để chuyển trọng tâm sang `get data -> label -> evaluate -> train`, nhưng README này phân biệt rõ giữa:
> **(1) phần đang chạy thật trong `main.py`** và **(2) các module additive mới đã implement xong để mở rộng sang data platform / training / observability**.  
> Các cải tiến kiến trúc đã hoàn tất trong code hiện tại: **equity sync**, **mode-gated isolation (consumer + producer + CCXT air-gap)**, **Postgres -> SQLite failover**, **Journalist dedupe**, **execution degradation surfacing**, **config centralization**, **API security + audit trail**, **runtime regression tests**, **collectors/data-tier foundation**, **backtest / portfolio / observability foundations**, và **Core AI orchestrator**.

ECONITH là một platform nghiên cứu định lượng và mô phỏng địa kinh tế gồm hai miền chủ quyền:

- **ECONITH Quant** — ingest dữ liệu thị trường, tạo feature, suy luận AI, route `order.intent`, kiểm soát rủi ro bằng Sentinel, hiển thị cockpit trading dashboard.
- **ECONITH World** — mô phỏng kinh tế vĩ mô, sovereign agents, chronology fork, scenario stress để nghiên cứu hiệu ứng lan truyền.

Vòng đời hệ thống hiện tại nên hiểu như sau:

```text
market/macro/tradfi -> event bus -> signal -> risk gate -> execution/fill -> telemetry -> dashboard
         |                             |
         +-> datasets/raw -----------> +-> label/evaluate/train -> registry -> inference
```

---

## 1. System Runtime Map

```text
main.py (FastAPI ASGI lifespan)
  |
  +-- Storage Bootstrap
  |    +-- init_database()             config/database.py
  |    +-- primary probe               Postgres SELECT 1 (5s timeout)
  |    +-- fallback                    SQLite failover when primary is unavailable
  |
  +-- API / Command Boundary
  |    +-- APIKeyAuthMiddleware        core/api/auth.py
  |    +-- AuditTrailLogger            operator mutation journal
  |
  +-- Core Engine
  |    +-- EventBus                    core/event_bus.py
  |    +-- TimeEngine                  core/engine.py
  |    +-- TickPipeline                SNAPSHOT -> APPLY_EVENTS -> RESOLVE_CONFLICTS -> UPDATE_WORLD -> EMIT_SIGNALS
  |    +-- QuantMode                   core/mode.py (REALITY | SIMULATION)
  |
  +-- Runtime Subscribers
  |    +-- MetricsHub                  core/telemetry.py
  |    +-- MarketDataPipeline          infrastructure/preprocessing/pipeline.py
  |    +-- StateRecorder               infrastructure/storage/recorder.py
  |    +-- Sentinel                    sentinel/manager.py
  |    +-- MacroIngestionHub           core/ingestion/macro_hub.py
  |    +-- CockpitTelemetryHub         core/cockpit/ws.py
  |    +-- JournalistLLM               ai/journalist/aggregator.py
  |    +-- CoreAIOrchestrator          ai/meta/core_ai.py
  |    +-- Predictor                   ai/inference/predictor.py
  |    +-- AIBridge                    econith_quant/bridge/ai_bridge.py
  |    +-- ExchangeBridge              econith_quant/bridge/exchange_bridge.py
  |    +-- QuantExecutionBridge        bridges/quant_bridge.py
  |    +-- WorldKernel                 ai/simulator_engine/world_kernel.py
  |    +-- SovereignWorldGraph         ai/simulator_engine/sovereign_graph.py
  |    +-- WorldBridge                 bridges/world_bridge.py
  |
  +-- Runtime Producers / Loops
  |    +-- BinanceWebSocketStreamer    infrastructure/websocket/streamer.py
  |    +-- AlternativeDataProvider     infrastructure/alternative/provider.py
  |    +-- CCXTBinanceBridge           quant/ccxt_bridge.py
  |    +-- Journalist flush loop
  |    +-- Core AI directive loop
  |    +-- Macro source schedulers
  |
  +-- UI / Delivery
       +-- REST endpoints              /api/v1/*
       +-- Metrics WS                  /api/v1/stream/metrics
       +-- Cockpit WS                  /api/v1/stream/cockpit
       +-- Next.js dashboard           dashboard/
```

### Những gì đang chạy thật trong `main.py`

- `EventBus`, `TimeEngine`, `TickPipeline`, `MetricsHub`, `MarketDataPipeline`, `StateRecorder`.
- `Sentinel` với execution-truth equity từ `quant.fill`.
- `MacroIngestionHub`, `CockpitTelemetryHub`, `JournalistLLM`, `CoreAIOrchestrator`.
- `Predictor`, `AIBridge`, `QuantExecutionBridge`, `CCXTBinanceBridge`.
- `WorldKernel`, `SovereignWorldGraph`, `WorldBridge`.
- API security middleware, audit trail, DB failover bootstrap.

### Những gì đã implement nhưng chưa phải hot-path production hoàn chỉnh

- `collectors/` — unit thu thập dữ liệu tách riêng để deploy VPS 24/7.
- `training/evaluation/backtest.py` — harness backtest vectorized.
- `ai/quant/portfolio.py` — allocator + portfolio VaR.
- `core/observability/logging.py` và `core/observability/alerts.py` — structured logs và webhook alerts.
- `training/quant/label_symbol.py` — labeler multi-symbol an toàn.

Nói ngắn gọn: **runtime chính đã chạy được**, còn **data/training platform mới đã có nền nhưng đang trong pha cut-over / operationalize**.

---

## 2. Kiến Trúc Vừa Được Cải Tiến

### 2.1 Runtime safety

- **Sentinel equity sync**: `Sentinel` không còn govern một “ghost equity” nữa; nó subscribe `quant.fill` và replay position/PnL theo cùng logic với `CockpitTelemetryHub`.
- **Mode-gated isolation**: `EventBus` hỗ trợ `domain=DOMAIN_QUANT` để drop mọi `world.*` tới order-routing nodes khi đang ở `REALITY`.
- **Producer air-gap**: `SovereignWorldGraph` chỉ phát `world.micro_impact` khi coupling thực sự được bật trong `SIMULATION`.
- **CCXT air-gap**: `CCXTBinanceBridge` tự dispose live session khi rời `REALITY`; không để live socket “kẹt” sang sandbox.
- **Execution degradation surfacing**: bridge expose `execution_status()` để `/api/v1/health` và dashboard biết khi runtime đang `LIVE`, `SYNTHETIC`, hoặc `DEGRADED`.
- **Database failover**: `init_database()` probe Postgres rồi failover có log sang SQLite khi primary không reachable.

### 2.2 Runtime quality / operator quality

- **Journalist anti-spam**: baseline comparison, digest dedupe, message dedupe, fact cooldown cho `world.micro_impact`, và giảm mức log routine xuống `debug`.
- **Quant cockpit layout refactor**: `/quant` dùng desktop grid rộng hơn, có persistent right-flank System Event Log, cockpit có thể collapse, flight log được trim, `simDay` chỉ hiện ở `SIMULATION`.
- **Dynamic Sentinel thresholds**: threshold local dev có thể nới qua env, đồng thời `CoreAIOrchestrator` có thể phát `meta.risk.directive` để tinh chỉnh trong biên an toàn.
- **API security + audit trail**: mutating routes có thể khóa bằng API key / bearer token; mọi mutation được ghi audit JSON lines.

### 2.3 Data / ML foundation

- **Collectors tier**: thêm `collectors/market_coin`, `collectors/macro_global`, `collectors/tradfi_assets` với `SnapshotWriter` ghi raw lake dạng partitioned Parquet.
- **Safe labeling**: `training/quant/label_symbol.py` sửa triệt để lỗi cross-symbol contamination bằng `groupby("symbol")`.
- **Evaluation layer**: `training/evaluation/backtest.py` thêm backtest vectorized, friction-aware, metrics đầy đủ.
- **Portfolio intelligence**: `ai/quant/portfolio.py` thêm allocator và correlation-aware portfolio VaR.
- **Observability foundation**: `core/observability` thêm JSON formatter, context-aware structured logs và webhook alert dispatcher.
- **Regression suite**: `tests/test_runtime.py` chốt 3 invariant quan trọng nhất: mode-gate, label safety, execution degradation visibility.

---

## 3. Operational Guardrails — cách cô lập REALITY / SIMULATION

`QUANT_MODE` là ranh giới chủ quyền dữ liệu. Việc cách ly được thực thi bằng **bốn tầng phòng vệ độc lập**:

| Tầng | Cơ chế | File |
|---|---|---|
| 1. Consumer gate | `EventBus` drop event `world.*` tới handler `DOMAIN_QUANT` khi `REALITY` | `core/event_bus.py` |
| 2. Producer air-gap | `SovereignWorldGraph` không phát `world.micro_impact` khi không ở `SIMULATION` | `ai/simulator_engine/sovereign_graph.py` |
| 3. Execution air-gap | `CCXTBinanceBridge` dispose live socket ngay khi rời `REALITY`; chỉ live khi authenticated | `quant/ccxt_bridge.py` |
| 4. Anomaly gate | Inject anomaly bị từ chối ngoài `SIMULATION` | `main.py`, `core/mode.py` |

### REALITY (mặc định an toàn)

- Quant chỉ ăn dữ liệu thật hoặc dữ liệu runtime thật (`Binance WS`, alt-data, macro runtime).
- Mọi `world.*` hướng vào order-routing bị chặn ở `EventBus` -> không look-ahead bias.
- World simulator vẫn chạy cho dashboard / scenario visibility nhưng không được phép contaminates execution.
- CCXT chỉ route live khi có credential thật và session authenticated; nếu không thì degrade sang synthetic với status rõ ràng.

### SIMULATION (sandbox nghiên cứu)

- Cho phép World tác động Quant qua `world.micro_impact`.
- Cho phép inject anomaly để stress-test.
- CCXT bị air-gap khỏi live socket; fill route sang synthetic by design.

### Equity truth (Sentinel <-> Cockpit)

`Sentinel` subscribe `quant.fill` và replay bằng đúng thuật toán position/PnL của Cockpit. `STARTING_CAPITAL` (mặc định `100000.0`) bind chung cho cả hai -> equity của Sentinel và Cockpit Fuel Gauge khớp 1:1. `md.ticker` chỉ còn vai trò mark-to-market + latency heartbeat.

### Database failover

`init_database()` probe primary DSN bằng `SELECT 1` (timeout 5s). Nếu Postgres unreachable -> log `CRITICAL` và failover sang `sqlite+aiosqlite:///econith_fallback.db`. Không bao giờ silent-fail.

```text
[DATABASE RUNTIME] Primary Postgres connection failed. Deploying local failover instance.
```

### Core AI governance note

`CoreAIOrchestrator` publish:

- `meta.quant.directive`
- `meta.risk.directive`
- `meta.world.directive`
- `meta.context`

Trong trạng thái code hiện tại, **`Sentinel` là consumer trực tiếp của `meta.risk.directive`**. Các directive còn lại đã có producer nhưng chưa được tiêu thụ sâu ở mọi sub-agent; chúng là nền cho pha wiring tiếp theo.

---

## 4. API Security & Audit Trail

Middleware `APIKeyAuthMiddleware` trong `core/api/auth.py` gate các route mutating nhạy cảm:

```text
/api/v1/mode
/api/v1/world/tariff
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

- Route đọc (`GET`) và WebSocket stream **không** bị gate -> dashboard và health checks vẫn hoạt động bình thường.
- Mọi lệnh mutation ghi vào audit trail xoay vòng `logs/econith_audit.log` dưới dạng JSON lines.
- Fingerprint khóa được hash SHA-256; không lưu raw API key.
- `API_AUTH_ENABLED=false` (mặc định local dev): route vẫn mở nhưng audit trail vẫn ghi `ALLOW_AUTH_DISABLED`.

---

## 5. EventBus Topic Contract

| Topic | Producer | Consumer chính / ghi chú |
|---|---|---|
| `md.aggTrade`, `md.depth` | `BinanceWebSocketStreamer` | `MarketDataPipeline` |
| `md.ticker` | `MarketDataPipeline` | `MetricsHub`, `Sentinel`, `CockpitTelemetryHub`, `QuantExecutionBridge`, `CoreAIOrchestrator` |
| `indicator.obi`, `indicator.volume_delta` | `MarketDataPipeline` | `Predictor`, `MetricsHub`, `CoreAIOrchestrator` |
| `alt.funding_rate`, `alt.open_interest`, `alt.liquidation` | `AlternativeDataProvider` | `Predictor`, `MetricsHub`, `CoreAIOrchestrator` |
| `ai.signal` | `Predictor` | `AIBridge`, telemetry |
| `order.intent` | AI / execution bridge | `QuantExecutionBridge` (`DOMAIN_QUANT`) |
| `quant.fill` | `CCXTBinanceBridge` | `CockpitTelemetryHub`, `Sentinel`, `JournalistLLM` |
| `sentinel.status`, `sentinel.emergency` | `Sentinel` | telemetry, recorder, dashboard |
| `core.macro.context` | `MacroIngestionHub` | cockpit, journalist, `CoreAIOrchestrator` |
| `world.sovereign`, `world.macro` | World simulator | World dashboard, journalist |
| `world.micro_impact` | `SovereignWorldGraph` | `JournalistLLM`, `CoreAIOrchestrator` (chỉ có hiệu lực trong `SIMULATION`) |
| `meta.quant.directive` | `CoreAIOrchestrator` | advisory topic, groundwork cho quant steering sâu hơn |
| `meta.risk.directive` | `CoreAIOrchestrator` | `Sentinel` |
| `meta.world.directive` | `CoreAIOrchestrator` | groundwork cho world-side adaptive steering |
| `meta.context` | `CoreAIOrchestrator` | consolidated read-model cho telemetry / dashboard tương lai |
| `journalist.news` | `JournalistLLM` | API terminal; UI news ticker đầy đủ vẫn là bước kế tiếp |

`EventBus` hiện là contract sống giữa runtime và các subsystem. Khi thêm consumer mới vào đường execution/risk, cần xác định rõ nó có phải `DOMAIN_QUANT` hay không.

---

## 6. Dashboard & Operator Surfaces

Frontend nằm ở `dashboard/` (`Next.js` / `React`).

### Quant page (`/quant`)

- `QuantMissionControl` là control deck chính cho trading runtime.
- Status bar hiển thị:
  - kết nối WS,
  - symbol đang theo dõi,
  - breaker state,
  - Sentinel mode,
  - `QUANT_MODE`,
  - execution route (`LIVE`, `SYNTHETIC`, `DEGRADED`, `OFFLINE`).
- Body desktop đã được refactor thành layout rộng hơn:
  - left flank: market strip, cockpit HUD, AI panel, Sentinel panel, operator controls;
  - right flank: persistent `System Event Log`.
- `QuantCockpitHUD` lấy dữ liệu từ `WS /api/v1/stream/cockpit`, gồm:
  - `PnL Altimeter`,
  - `Margin Fuel Gauge`,
  - `Flight Log`,
  - `Allocation Radar`.
- `simDay` chỉ hiển thị khi runtime ở `SIMULATION`.

### World page (`/world`)

- Hiển thị sovereign snapshot, chronology fork, scenario mutations và world state read-model.
- World vẫn chạy ở `REALITY` để quan sát, nhưng không được quyền contaminate execution path.

### Journalist / news

- `JournalistLLM` đã có terminal data path và API read-model.
- `GET /api/v1/journalist/news` hoạt động.
- UI news ticker chuyên biệt vẫn là bước tiếp theo; hiện tại dữ liệu đã sẵn sàng nhưng chưa có mặt hiển thị hoàn chỉnh.

### Endpoint bề mặt cho UI / operator

```text
GET  /api/v1/health
GET  /api/v1/metrics
GET  /api/v1/time
GET  /api/v1/mode
GET  /api/v1/state
GET  /api/v1/world/state
GET  /api/v1/world/country/{code}
GET  /api/v1/world/sovereign
GET  /api/v1/world/chronology
GET  /api/v1/macro/snapshot
GET  /api/v1/journalist/news
GET  /api/v1/cockpit
GET  /api/v1/cockpit/snapshot
WS   /api/v1/stream/metrics
WS   /api/v1/stream/cockpit
POST /api/v1/mode
POST /api/v1/world/scenario
POST /api/v1/world/tariff
POST /api/v1/world/country/{code}/mutate
POST /api/v1/sentinel/inject
POST /api/v1/sentinel/reset
POST /api/v1/time/speed
POST /api/v1/time/pause
POST /api/v1/time/resume
```

---

## 7. Cấu trúc thư mục quan trọng

```text
econith/
├── ai/
│   ├── agents/                       # quant desk logic / model loaders (legacy live path)
│   ├── inference/predictor.py        # live inference node đang được main.py dùng
│   ├── journalist/                   # Journalist LLM + terminal synthesis
│   ├── meta/                         # Core AI orchestrator
│   ├── quant/                        # portfolio intelligence foundations
│   ├── regime/                       # regime classifier / switcher
│   └── simulator_engine/             # World kernel + sovereign graph + scenarios
├── bridges/
│   ├── quant_bridge.py               # order.intent -> CCXT/synthetic (DOMAIN_QUANT)
│   └── world_bridge.py               # legacy kernel <-> sovereign graph bridge
├── collectors/
│   ├── README.md                     # deploy guide cho VPS data collection
│   ├── requirements.txt              # lightweight deps only
│   ├── shared/                       # schemas / partitioning / SnapshotWriter
│   ├── market_coin/                  # 24/7 crypto collector
│   ├── macro_global/                 # scheduled macro collector
│   └── tradfi_assets/                # session-based tradfi poller
├── config/
│   ├── database.py                   # async DB + Postgres->SQLite failover
│   ├── environment.py                # typed env (STARTING_CAPITAL, API auth...)
│   └── settings.py                   # centralized settings surface
├── core/
│   ├── api/auth.py                   # API key/bearer middleware + audit trail
│   ├── cockpit/                      # cockpit schemas + WS router
│   ├── ingestion/                    # macro ingestion hub/adapters
│   ├── observability/                # JSON logging + webhook alerts (foundation)
│   ├── engine.py                     # deterministic 5-phase engine
│   ├── event_bus.py                  # pub/sub + mode governance gate
│   ├── mode.py                       # REALITY/SIMULATION singleton
│   └── telemetry.py                  # dashboard read model
├── dashboard/                        # Next.js operator UI
├── docs/
│   └── RESTRUCTURE_BLUEPRINT.md      # target 4-tier restructure contract
├── econith_quant/                    # vendored quant substrate / bridges / training helpers
├── infrastructure/
│   ├── alternative/  daemon/  preprocessing/  storage/  websocket/
├── datasets/
│   ├── raw/                          # append-only raw lake cho collectors
│   ├── processed/                    # labeled / merged training data
│   └── ...                           # features / tensor cache / sqlite tùy workflow
├── quant/
│   ├── ccxt_bridge.py                # live/synthetic execution + air-gap
│   ├── context_slicer.py
│   └── payloads.py
├── sentinel/
│   ├── manager.py                    # execution-truth risk governor
│   ├── circuit_breaker.py
│   └── var.py
├── tests/
│   └── test_runtime.py               # regression suite cho invariants quan trọng
├── training/
│   ├── collect.py  label.py  orchestrator.py  deploy.py   # legacy factory entrypoints
│   ├── evaluation/backtest.py        # vectorized backtest + metrics
│   ├── h200/orchestrator.py          # async dataloader + DDP + registry writeout
│   ├── quant/label_symbol.py         # multi-symbol-safe labeler
│   ├── train_ppo.py  fit_regime.py  train_world.py        # legacy trainers
│   └── ...
├── Makefile                          # one-word entrypoints cho local factory flow
└── main.py
```

---

### Cách hiểu đúng cấu trúc hiện tại

- **Live runtime hôm nay** vẫn còn dùng một số đường legacy như `ai/inference/predictor.py`, `ai/agents/`, `ai/regime/`, `training/*.py`.
- **Kiến trúc mục tiêu mới** đã được dựng additive qua `collectors/`, `training/quant/`, `training/evaluation/`, `ai/meta/`, `ai/quant/`, `core/observability/`.
- Vì vậy, repo hiện tại là **một trạng thái chuyển tiếp có chủ đích**: không broken, nhưng chưa cut-over hoàn toàn sang layout mới.

---

## 8. Data / Training Architecture Đúng Nhất Hiện Tại

### Bốn tầng logic của dự án

| Tầng | Vai trò | Trạng thái hiện tại |
|---|---|---|
| `collectors/` | unit thu thập dữ liệu độc lập, zero-ML, deploy VPS | đã implement |
| `datasets/` | raw lake + processed feature store | đã có nền / dùng dần |
| `training/` | labeling, evaluation, orchestration, H200 training | đã có cả legacy path và path mới |
| `ai/` + runtime | inference, execution, risk, world simulation, dashboard | đang chạy thật |

### Boundary rules

- `collectors/` chỉ nên dùng stdlib + `polars` / `pandas` / `pyarrow` / `websockets` / `httpx`.
- `collectors/` **không** nên import `ai/`, `training/`, `torch`, hay runtime FastAPI.
- `training/` có thể dùng dữ liệu từ `collectors/` và artifact từ runtime, nhưng không nên gắn chặt với UI.
- `ai/` và `main.py` tiêu thụ artifact đã train, không nên trực tiếp trở thành daemon thu thập raw data dài ngày.

### Hai đường dữ liệu hiện đang song song tồn tại

#### 1. Legacy local feature path

- Dùng `training/collect.py`, `training/label.py`, `training/orchestrator.py`, `Makefile`.
- Phù hợp cho local iteration nhanh hoặc backward compatibility.
- Không phải kiến trúc cuối cùng cho chiến dịch thu thập data nhiều coin / 24x7 / nhiều lớp tài sản.

#### 2. New decoupled raw-lake path

- Dùng `collectors/market_coin`, `collectors/macro_global`, `collectors/tradfi_assets`.
- Ghi dữ liệu append-only xuống `datasets/raw/...`.
- Sau đó mới label / evaluate / train ở tier `training/`.
- Đây là hướng đúng để đẩy lên VPS treo 24/7 và gom data chất lượng dài ngày.

### Correct logic cho data pipeline

```text
collectors/* -> datasets/raw/* -> feature engineering / merge-asof
             -> training/quant/label_symbol.py
             -> datasets/processed/*
             -> training/evaluation/backtest.py
             -> training/h200/orchestrator.py
             -> models/registry/*
             -> runtime inference
```

### Correct logic cho labeling

`training/quant/label_symbol.py` là đường đúng về mặt toán học cho multi-symbol:

- tính forward return **bên trong** từng `groupby("symbol")`,
- split train / holdout theo thời gian **trên từng symbol**,
- tránh hoàn toàn cross-contamination giữa BTC, ETH, DOGE, meme coin, v.v.

### Correct logic cho collectors

- `collectors.market_coin.daemon`:
  - tự phục hồi websocket,
  - multi-symbol,
  - flush định kỳ bằng `SnapshotWriter`,
  - fallback sang synthetic tape nếu thiếu `websockets`.
- `collectors.macro_global.scheduler`:
  - poll theo cadence,
  - hiện có FRED là keyed source chính,
  - append snapshot point-in-time.
- `collectors.tradfi_assets.poller`:
  - poll Yahoo Finance chart endpoint,
  - lưu đúng trạng thái session open/closed, không fabricate dữ liệu.

### Correct logic cho persistence của collectors

`SnapshotWriter`:

- ưu tiên `polars`,
- fallback sang `pandas`,
- cuối cùng fallback sang `jsonl` nếu máy VPS còn quá tối giản.

Điểm quan trọng: collector **không được chết chỉ vì thiếu một backend ghi file**.

---

## 9. Quick-Start Blueprint

### 9.1 Backend — Development

```powershell
cd f:\econith
pip install -r requirements.txt
python main.py
# hoặc: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 9.2 Backend — Production

```bash
# Production: tắt reload, nhiều worker, bật auth
export APP_ENV=production
export API_AUTH_ENABLED=true
export API_KEYS=$(openssl rand -hex 24)
export DATABASE_URL=postgresql://econith:econith@postgres:5432/econith

uvicorn main:app \
  --host 0.0.0.0 --port 8000 \
  --workers 1 \
  --no-server-header \
  --proxy-headers --forwarded-allow-ips="*"
```

> Lưu ý: mỗi uvicorn worker là một process độc lập với `EventBus` / `TimeEngine` / runtime state riêng. Với kiến trúc stateful hiện tại, **khuyến nghị dùng 1 worker** cho backend giao dịch. Chỉ scale multi-instance khi đã externalize state đúng cách.

### 9.3 Frontend — Development / Production

```powershell
cd f:\econith\dashboard
npm install
npm run dev          # dev
# production:
npm run build
npm run start
```

Mở: `http://localhost:3000`, `/quant`, `/world`.

### 9.4 Health check

```powershell
curl http://localhost:8000/api/v1/health
```

Kỳ vọng an toàn:

```json
{
  "status": "ok",
  "service": "backend_core",
  "quant_mode": {
    "mode": "REALITY",
    "coupling_enabled": false,
    "anomaly_injection_enabled": false
  },
  "execution": {
    "execution_routing": "SYNTHETIC"
  }
}
```

---

### 9.5 Runtime regression tests

```powershell
pip install -r requirements-dev.txt
pytest -q
```

Test suite hiện chốt 3 invariant:

- `REALITY` không nhận world contamination vào `DOMAIN_QUANT`,
- labeling multi-symbol không cross-contaminate,
- CCXT degradation nổi lên rõ ràng ở health read-model.

---

## 10. Data Acquisition & H200 Training Runbook

### 10.1 Mode switching & World scenario

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

### 10.2 Local factory path (nhanh, legacy nhưng vẫn dùng được)

```powershell
make help
make setup-train
make data-collect
make data-collect-backfill
make train-all
```

Ghi chú quan trọng:

- `Makefile` hiện vẫn front một số script legacy dưới `training/*.py`.
- Đường này phù hợp cho local iteration và backward compatibility.
- Nếu mục tiêu là chiến dịch data 24/7 nhiều coin, nhiều lớp tài sản, nên dùng collectors path ở dưới.

### 10.3 Standalone collectors path (khuyến nghị cho VPS / long-running)

```powershell
# Coin live collector
python -m collectors.market_coin.daemon

# Macro scheduler
python -m collectors.macro_global.scheduler

# TradFi poller
python -m collectors.tradfi_assets.poller
```

Output đi vào raw lake theo partition, ví dụ:

```text
datasets/raw/market/...
datasets/raw/macro/...
datasets/raw/tradfi/...
```

Lưu ý: `datasets/raw` là **raw lake**, chưa phải input trực tiếp cho labeler. Cần có bước feature engineering / merge-asof để tạo tập feature trước khi chạy labeling.

### 10.4 Label an toàn cho multi-symbol

```powershell
python -m training.quant.label_symbol `
  --input ./datasets/features `
  --output ./datasets/processed/quant_labeled.parquet `
  --holdout-ratio 0.20
```

Lưu ý rất quan trọng:

- `make data-label` hiện vẫn gọi `training/label.py` (legacy path).
- Với dataset nhiều symbol, **nên ưu tiên chạy trực tiếp** `training.quant.label_symbol`.
- Đây là đường đúng để tránh lỗi trộn timeline giữa các asset.

### 10.5 Backtest / evaluation layer

`training/evaluation/backtest.py` là harness kiểm định offline hiện tại:

- vectorized,
- net-of-cost,
- có fee / slippage / spread friction,
- xuất Sharpe, Sortino, Max Drawdown, Profit Factor, Win Rate, Turnover, PnL by Regime.

Nó phù hợp để chèn vào pipeline sau labeling và trước training / deploy.

### 10.6 Mount dataset lên RunPod H200

```bash
cd /workspace/econith
pip install -r requirements.txt
pip install -r requirements-train.txt

# đưa parquet / processed set lên pod bằng volume mount / rsync / object storage
ls datasets
```

### 10.7 Multi-GPU training harness (`training/h200/orchestrator.py`)

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
    parquet_root="datasets/processed",
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

### 10.8 Verify + deploy an toàn

```powershell
make model-verify     # đối chiếu SHA-256 từng checkpoint theo manifest
make model-deploy     # activate.yaml + archive history rollback
```

Rollback:

```powershell
python training/deploy.py --rollback
```

### 10.9 Restart backend nạp model mới

```powershell
python main.py
```

Sau restart, `ai.signal` sẽ có `agent_brain=trained` / `regime_brain=trained` khi checkpoint hợp lệ.

---

## 11. Roadmap tiếp theo

### P0 — Data / training cut-over

1. Chốt collectors chạy thật trên VPS 24/7 cho coin, macro, tradfi.
2. Xây bước merge / processed feature store từ `datasets/raw`.
3. Chuyển mặc định từ `training/label.py` sang `training/quant/label_symbol.py`.
4. Quy hoạch lại `Makefile` để front đúng các path mới thay vì legacy-only.

### P1 — Evaluation / model governance

5. Gắn `training/evaluation/backtest.py` thành bước chính thức trước deploy.
6. Ghi dataset hash + metrics + code hash vào registry chặt hơn.
7. Chạy paper campaign dài ngày với execution routing và Sentinel telemetry được theo dõi liên tục.

### P2 — Runtime capability expansion

8. Wire sâu hơn `meta.quant.directive` và `meta.world.directive` vào sub-agents.
9. Đưa `ai/quant/portfolio.py` vào sizing / de-risking hot path.
10. Hoàn thiện news ticker UI đọc `journalist.news`.

### P3 — Observability / production hardening

11. Bật `configure_json_logging()` thật trong bootstrap runtime.
12. Nối `AlertDispatcher` vào các sự kiện quan trọng: `db_failover`, `exchange_degraded`, `sentinel_freeze`, `ws_disconnect`.
13. Thêm Prometheus / Grafana / Docker Compose / service supervision cho collectors và backend.

---

## 12. Security rules

- Không commit `.env`.
- Không chụp/chia sẻ ảnh chứa API key/secret.
- Bật `API_AUTH_ENABLED=true` + `API_KEYS` mạnh trước khi expose backend.
- Dùng Binance testnet trước khi bật trade credential thật.
- Nếu nghi lộ key: revoke ngay, tạo key mới, restart backend.
- Không expose port backend public nếu chưa có auth + TLS.

---

## 13. Engineering state hiện tại

### Đã ổn và dùng được

- Runtime event-driven đầy đủ, deterministic 5-phase tick.
- Quant / World sovereignty có 4 tầng cô lập.
- Sentinel dùng execution-truth equity, khớp Cockpit 1:1.
- CCXT live/synthetic execution có air-gap và surfacing trạng thái degradation.
- Database failover không còn silent fail.
- Quant dashboard đã đủ dùng như mission control thật cho local runtime.
- Runtime regression suite đã chốt các invariant quan trọng nhất.

### Đã có nền nhưng chưa operationalize hoàn toàn

- `collectors/` đã implement nhưng chưa phải ingest path mặc định của toàn dự án.
- `training/evaluation/backtest.py` đã có nhưng chưa được ép thành gate bắt buộc trước deploy.
- `ai/quant/portfolio.py` đã có nhưng chưa đi vào sizing hot path.
- `core/observability` đã có nhưng chưa được bật toàn cục trong `main.py`.
- `meta.quant.directive` và `meta.world.directive` đã được publish nhưng chưa có consumer sâu ở mọi sub-agent.

### Kết luận kỹ thuật ngắn gọn

**Ngoài data collection và training cut-over, nền tảng hiện tại đã đủ ổn để chuyển phase.**

Việc tiếp theo hợp lý nhất là:

1. chạy collectors thật dài ngày,
2. build processed feature store sạch,
3. label bằng path multi-symbol-safe,
4. backtest,
5. rồi đưa lên H200 để train và registry hóa artifact.
