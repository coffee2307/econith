# ECONITH — Quant + World Runtime Architecture

> Cập nhật: 03/07/2026 (UTC+7)  
> Trạng thái hiện tại: backend ASGI boot được, dashboard Quant/World hoạt động, cockpit WS đã có, và 3 khoản nợ P0 đã được xử lý: **equity sync**, **mode-gated isolation**, **database failover**.

ECONITH là hệ thống nghiên cứu định lượng kết hợp hai miền tách biệt:

- **ECONITH Quant**: ingest dữ liệu thị trường, tạo feature, chạy AI decision, route order intent, kiểm soát rủi ro bằng Sentinel, hiển thị cockpit trading dashboard.
- **ECONITH World**: mô phỏng kinh tế-vĩ mô-địa chính trị bằng sovereign multi-agent graph để stress-test, tạo kịch bản và nghiên cứu hiệu ứng lan truyền.

Mục tiêu của dự án là xây một vòng đời hoàn chỉnh:

```text
live data -> feature -> signal -> risk gate -> execution/fill -> cockpit telemetry
                 |
                 v
        collect -> label -> train -> verify -> activate -> inference
```

---

## 1. Kiến trúc runtime hiện tại

```text
main.py (FastAPI ASGI lifespan)
  |
  +-- Core Engine
  |    +-- EventBus                  core/event_bus.py
  |    +-- TimeEngine                core/engine.py
  |    +-- 5-phase TickPipeline       SNAPSHOT -> APPLY_EVENTS -> RESOLVE_CONFLICTS -> UPDATE_WORLD -> EMIT_SIGNALS
  |    +-- QuantMode                 core/mode.py (REALITY | SIMULATION)
  |
  +-- Market Data Plane
  |    +-- BinanceWebSocketStreamer  infrastructure/websocket/streamer.py
  |    +-- MarketDataPipeline        infrastructure/preprocessing/pipeline.py
  |    +-- AlternativeDataProvider   infrastructure/alternative/provider.py
  |    +-- MacroIngestionHub         core/ingestion/macro_hub.py
  |
  +-- Quant Brain
  |    +-- Predictor                 ai/inference/predictor.py
  |    +-- Regime + desk fusion       ai/regime/, ai/agents/
  |    +-- AIBridge                  econith_quant/bridge/ai_bridge.py
  |    +-- QuantExecutionBridge      bridges/quant_bridge.py
  |    +-- CCXTBinanceBridge         quant/ccxt_bridge.py
  |
  +-- Risk + Persistence
  |    +-- Sentinel                  sentinel/manager.py
  |    +-- CircuitBreaker            sentinel/circuit_breaker.py
  |    +-- StateRecorder             infrastructure/storage/recorder.py
  |    +-- DB failover               config/database.py
  |
  +-- World Simulator
  |    +-- WorldKernel               ai/simulator_engine/world_kernel.py
  |    +-- SovereignWorldGraph       ai/simulator_engine/sovereign_graph.py
  |    +-- WorldBridge               bridges/world_bridge.py
  |
  +-- User Interfaces
       +-- MetricsHub                core/telemetry.py
       +-- CockpitTelemetryHub       core/cockpit/ws.py
       +-- JournalistLLM             ai/journalist/aggregator.py
       +-- Next.js Dashboard         dashboard/
```

---

## 2. Hai thế giới vận hành: REALITY và SIMULATION

`QUANT_MODE` là ranh giới chủ quyền dữ liệu:

| Mode | Ý nghĩa | Data plane | Execution |
|---|---|---|---|
| `REALITY` | Chế độ mặc định an toàn | Dữ liệu thị trường thật / Binance / macro thật | Chỉ được dùng live/testnet nếu CCXT đã authenticated; `world.*` bị chặn khỏi domain Quant |
| `SIMULATION` | Sandbox nghiên cứu | World simulator được phép tác động Quant | CCXT bị air-gap khỏi live socket; fill route sang synthetic simulation |

### Invariant quan trọng

Trong `REALITY`, Quant và World **không được trộn dữ liệu**. World có thể vẫn chạy để dashboard hiển thị, nhưng mọi event `world.*` hướng vào order-routing domain sẽ bị `EventBus` drop.

Trong `SIMULATION`, Quant có thể ăn synthetic vectors của World, nhưng CCXT không được giữ live network/order socket.

---

## 3. Các P0 đã xử lý

### 3.1 Equity sync: Sentinel và Cockpit dùng cùng execution truth

File chính: `sentinel/manager.py`, `core/cockpit/ws.py`

Trước đây:

- Cockpit Fuel Gauge tính equity từ `quant.fill`.
- Sentinel tự tính equity từ mock budget `$1,000,000` + price move.
- Kết quả là hai panel hiển thị hai sổ vốn khác nhau.

Hiện tại:

- Sentinel subscribe trực tiếp `quant.fill`.
- Sentinel replay fill bằng cùng logic position/PnL với cockpit.
- `md.ticker` chỉ dùng để mark-to-market open positions và đo latency.
- Equity của Sentinel và Cockpit Fuel Gauge khớp 1:1.

### 3.2 Mode-gated EventBus

File chính: `core/event_bus.py`, `bridges/quant_bridge.py`, `quant/ccxt_bridge.py`

Đã thêm:

- `DOMAIN_QUANT` cho các handler thuộc order-routing/execution.
- `EventBus.subscribe(..., domain=DOMAIN_QUANT)`.
- Khi `QuantMode.REALITY`, event `world.*` bị drop trước khi tới handler domain Quant.
- `CCXTBinanceBridge` có mode-change guard: rời `REALITY` là dispose live exchange session ngay.

### 3.3 Database failover

File chính: `config/database.py`, `main.py`

Đã thêm:

- `init_database()` probe database primary bằng `SELECT 1` với timeout.
- Nếu Postgres lỗi/unreachable: log `CRITICAL` và failover sang `sqlite:///econith_fallback.db`.
- `dispose_database()` chạy lúc ASGI shutdown.

Thông điệp log kỳ vọng khi failover:

```text
[DATABASE RUNTIME] Primary Postgres connection failed. Deploying local failover instance.
```

---

## 4. EventBus topic contract

| Topic | Producer | Consumer chính |
|---|---|---|
| `md.aggTrade`, `md.depth` | `BinanceWebSocketStreamer` | `MarketDataPipeline` |
| `md.ticker` | `MarketDataPipeline` | `MetricsHub`, `Sentinel`, `CockpitTelemetryHub`, `QuantExecutionBridge` |
| `indicator.obi`, `indicator.volume_delta` | `MarketDataPipeline` | `Predictor`, `MetricsHub` |
| `alt.funding_rate`, `alt.open_interest`, `alt.liquidation` | `AlternativeDataProvider` | `Predictor`, `MetricsHub` |
| `ai.signal` | `Predictor` | `AIBridge`, telemetry |
| `order.intent` | AI/execution bridge | `QuantExecutionBridge` |
| `quant.fill` | `CCXTBinanceBridge` | `CockpitTelemetryHub`, `Sentinel`, `JournalistLLM` |
| `sentinel.status` | `Sentinel` | `MetricsHub`, `StateRecorder`, dashboard |
| `sentinel.emergency` | `Sentinel` | telemetry, event log |
| `core.macro.context` | `MacroIngestionHub` | cockpit, journalist |
| `world.sovereign`, `world.macro` | World simulator | World dashboard, cockpit macro strip, journalist |
| `journalist.news` | `JournalistLLM` | future news ticker |

---

## 5. Dashboard hiện tại

Frontend nằm trong `dashboard/`.

Đã có:

- Landing page nâng cấp animation/scroll effects.
- Quant Mission Control full-width desktop layout.
- Right-flank persistent System Event Log.
- Cockpit HUD qua WS `/api/v1/stream/cockpit`.
- Widgets cockpit:
  - PnL Altimeter
  - Margin Fuel Gauge
  - Flight Log
  - Allocation Radar
- World simulator page.

Các endpoint dashboard dùng:

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
POST /api/v1/mode
POST /api/v1/world/tariff
POST /api/v1/world/mutate
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
│   ├── quant_bridge.py               # order.intent -> CCXT/synthetic fill
│   └── world_bridge.py               # World API bridge
├── config/
│   ├── database.py                   # async DB + Postgres->SQLite failover
│   ├── environment.py                # env parsing
│   └── settings.py                   # API/app settings
├── core/
│   ├── cockpit/                      # cockpit schemas + WS router
│   ├── ingestion/                    # macro ingestion hub/adapters
│   ├── engine.py                     # deterministic 5-phase engine
│   ├── event_bus.py                  # pub/sub + mode governance gate
│   ├── mode.py                       # REALITY/SIMULATION singleton
│   └── telemetry.py                  # dashboard read model
├── infrastructure/
│   ├── alternative/                  # funding/OI/liquidation provider
│   ├── daemon/                       # VPS telemetry daemon framework
│   ├── preprocessing/                # OBI/volume-delta pipeline
│   ├── storage/                      # SQLite recorder/store
│   └── websocket/                    # Binance WS streamer
├── quant/
│   ├── ccxt_bridge.py                # live/synthetic execution bridge
│   ├── context_slicer.py             # desk context slicing
│   └── payloads.py                   # execution payload contracts
├── sentinel/
│   ├── manager.py                    # execution-truth risk governor
│   ├── circuit_breaker.py
│   └── var.py
├── training/
│   ├── collect.py
│   ├── label.py
│   ├── orchestrator.py
│   ├── train_ppo.py
│   ├── fit_regime.py
│   ├── train_world.py
│   ├── deploy.py
│   └── h200/orchestrator.py
├── dashboard/
└── main.py
```

---

## 7. Khởi chạy nhanh

### Backend

```powershell
cd f:\econith
python main.py
```

Hoặc:

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Dashboard

```powershell
cd f:\econith\dashboard
npm install
npm run dev
```

Mở:

```text
http://localhost:3000
http://localhost:3000/quant
http://localhost:3000/world
```

### Health check

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

## 8. Mode switching

Chuyển sang sandbox simulation:

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/mode -H "Content-Type: application/json" -d "{\"mode\":\"SIMULATION\"}"
```

Quay về reality:

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/mode -H "Content-Type: application/json" -d "{\"mode\":\"REALITY\"}"
```

Trong `REALITY`, Quant không hiển thị World simulation day và không nhận `world.*` vào execution domain.

---

## 9. World scenario API

Inject tariff shock:

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/world/tariff -H "Content-Type: application/json" -d "{\"source\":\"USA\",\"target\":\"CHN\",\"value\":0.5}"
```

Mutate world metric:

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/world/mutate -H "Content-Type: application/json" -d "{\"group\":\"\",\"field\":\"inflation\",\"value\":0.04}"
```

---

## 10. Training pipeline

Pipeline vẫn giữ vòng đời A→E:

```text
collect -> label -> train -> verify -> activate -> rollback
```

Lệnh chính:

```powershell
make data-collect
make data-label
make setup-train
make train-all
make model-verify
make model-deploy
```

Artifacts chính:

```text
models/agents/trend_ppo.zip
models/agents/mean_reversion_ppo.zip
models/agents/scalper_ppo.zip
models/regime/hmm_4state.pkl
models/world/neural_reaction.pt
models/registry/manifest.yaml
models/registry/active.yaml
```

RunPod/H200 framework nằm ở `training/h200/orchestrator.py`, hiện là khung orchestration cần nối tiếp vào dataset loader/checkpoint store thật.

---

## 11. Những gì cần làm tiếp theo

### P0 — Stabilization trước khi thêm feature lớn

1. **Viết test suite chính thức cho 3 invariant vừa sửa**
   - `Sentinel` equity phải khớp cockpit sau `quant.fill`.
   - `EventBus` phải drop `world.*` vào `DOMAIN_QUANT` trong `REALITY`.
   - `config.database.init_database()` phải failover đúng khi Postgres unreachable.

2. **Tách mode producer rõ hơn**
   - `REALITY`: ưu tiên Binance/live market stream.
   - `SIMULATION`: ưu tiên synthetic/world feed.
   - Mục tiêu: không chỉ drop ở consumer mà còn giảm producer thừa.

3. **Đồng bộ vốn khởi tạo qua config**
   - Đưa `STARTING_CAPITAL` vào `.env` / settings.
   - Dùng chung cho `CockpitTelemetryHub`, `Sentinel`, và simulation executor.

### P1 — Production readiness

4. **Auth cho API/dashboard**
   - Bảo vệ `/mode`, `/world/mutate`, `/world/tariff`, order/control endpoints.
   - Tối thiểu: API key middleware; tốt hơn: operator login/OAuth.

5. **Rate limit + command guard**
   - Chặn spam mutate/world scenario.
   - Thêm audit trail cho mọi operator command.

6. **Structured logging + observability**
   - JSON logs.
   - Prometheus metrics: tick latency, WS reconnects, fills, Sentinel freezes, DB failover.
   - Grafana dashboard.

7. **News ticker UI**
   - Backend đã có `JournalistLLM` và `/journalist/news`.
   - Cần widget cockpit hiển thị `journalist.news`.

8. **LLM backend thật cho Journalist**
   - Hiện deterministic template backend.
   - Cần OpenAI/Anthropic/Ollama backend qua env.

### P2 — Quant capability

9. **Multi-symbol portfolio**
   - Mở rộng khỏi BTCUSDT.
   - Portfolio-level VaR, exposure per desk, per-asset limits.

10. **Backtesting harness**
   - Reuse SIMULATION mode + historical feed.
   - Báo cáo hit-rate, drawdown, turnover, profit factor, stability.

11. **Paper trading campaign**
   - Chạy Binance testnet/paper đủ dài.
   - Lưu report trước khi nghĩ tới vốn thật.

12. **Model governance**
   - Gắn commit hash, dataset hash, training window, metrics vào `manifest.yaml`.
   - Dashboard hiển thị active model version.

### P3 — Scaling

13. **H200 training pipeline thật**
   - Nối `training/h200` với dataset registry, checkpoint store, distributed runner.

14. **VPS daemon deployment**
   - Thêm Docker/systemd service cho `infrastructure/daemon/vps_telemetry_daemon.py`.

15. **Docker compose production bundle**
   - Backend + dashboard + Postgres + Redis + Prometheus + Grafana.

---

## 12. Security rules

- Không commit `.env`.
- Không chụp màn hình chứa API key/secret.
- Dùng Binance testnet trước khi bật trade credential thật.
- Nếu key nghi bị lộ: revoke ngay, tạo key mới, restart backend.
- Không expose port backend public nếu chưa có auth.

---

## 13. Current engineering state

Hệ thống hiện đã vượt qua giai đoạn "khung demo":

- Có runtime event-driven đầy đủ.
- Có Quant/World mode sovereignty.
- Có cockpit UI và system event log persistent.
- Có Sentinel dùng execution-truth equity.
- Có CCXT live/synthetic execution bridge với air-gap.
- Có database failover thay vì silent fail.

Việc tiếp theo không phải thêm nhiều màn hình hơn, mà là **đóng test, bảo mật, quan sát hệ thống, rồi mới mở rộng multi-symbol và paper campaign**.
