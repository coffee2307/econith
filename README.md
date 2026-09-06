# ECONITH — Quant + World Production Runtime

> **Cập nhật báo cáo:** 03/08/2026 (UTC+7)  
> **Phase hiện tại:** `SIM / DEMO + heuristic desks` — runtime đầy đủ; **lớp đánh giá khoa học KHKT** (RQ1–RQ4) đã có trong repo; pipeline train→deploy→gate đã có trong code; **chưa** có desk SB3 đã train trên máy local / `active.yaml` production.  
> **FULLY_AUTONOMOUS:** đúng là **flag-only** (`AUTONOMOUS_LOOP_IMPLEMENTED=False`) — không bật cho đến khi deploy gate + paper soak xanh.  
> Train nặng: **H200/cloud**. Máy yếu: World OFF + `LOCAL_LLM_ENABLED=false` + hyp combinatorial (xem preset trong `.env.example`).

ECONITH là một platform nghiên cứu định lượng và mô phỏng địa kinh tế gồm hai miền chủ quyền:

- **ECONITH Quant** — ingest dữ liệu thị trường, tạo feature, suy luận AI, route `order.intent`, kiểm soát rủi ro bằng Sentinel, hiển thị cockpit trading dashboard.
- **ECONITH World** — mô phỏng kinh tế vĩ mô, sovereign agents, chronology fork, hypothesis/scenario stress để nghiên cứu hiệu ứng lan truyền (mặc định **tắt** trên máy yếu).

Vòng đời hệ thống hiện tại nên hiểu như sau:

```text
market/macro/tradfi -> event bus -> signal -> risk gate -> execution/fill -> telemetry -> dashboard
         |                             |
         +-> datasets/raw -----------> +-> label/evaluate/train -> registry -> inference
         |
         +-> World hyp/rollouts -----> sealed JSONL -> ingest -> (sau này) PPO dataset
         |
         +-> KHKT_Evaluation --------> B0/E1/C1 · calibration · ablation · reproducibility
```

---

## 0. Báo cáo trạng thái dự án (Progress Report)

Phần này là **file báo cáo vận hành / kỹ thuật**: đã làm gì, đang đứng ở đâu, còn thiếu gì. Đọc section này trước nếu bạn muốn biết “xong bao nhiêu % product claim”.

### 0.1 Tóm tắt một đoạn

| Trục | Trạng thái | Ý nghĩa cho operator |
|------|------------|----------------------|
| Runtime Quant + World + Dashboard | **Chạy được** (`main.py` + `npm run dev`) | SIM/DEMO đủ dùng nghiên cứu & UI |
| Isolation REALITY / SIMULATION | **Đã cứng** (4 tầng) | Không để World “nhiễm” order path ở REALITY |
| Desk AI Quant | **Heuristic** (trừ khi có `models/registry/active.yaml` hợp lệ) | Main Control sẽ hiện `agent_brain=heuristic` cho đến khi train/deploy |
| Deploy customs + backtest gate | **Đã có trong code** | `--activate` fail nếu backtest không đạt (trừ `--skip-backtest`) |
| Neural World `react()` | **Đã wire** (chỉ live khi có `.pt`) | Không checkpoint = stack heuristic CB/Trade/Sentiment |
| World UI 50 nước | **Honesty** — chỉ 6 hub mutate live | Globe không còn overclaim “50 nước = backend” |
| Meta directives | **Có consumer thật** trên hot path | Quant sizing + Predictor lean + World micro shock |
| Paper / DEMO soak | **Checklist ops** (`scripts/paper_soak_check`) | Chưa thay bằng campaign N ngày có nhật ký PnL thật |
| **KHKT evaluation (RQ1–RQ4)** | **Đã có suite + số liệu** (`KHKT_Evaluation/`) | Protocol B0/E1/C1, calibration, ablation, reproducibility — độc lập demo UI |
| Hypothesis smart / locale | **Đã nâng cấp** | Debate/thinker/i18n; `HYPOTHESIS_USE_LLM=false` mặc định cho tái lập |
| `FULLY_AUTONOMOUS` | **Flag-only** | Không auto retrain→deploy |

```mermaid
flowchart LR
  now[SIM_DEMO_heuristic]
  data[Data_and_labels]
  train[H200_train_PPO]
  deploy[active_yaml_desks]
  neural[World_react_head]
  paper[Paper_soak]
  khkt[KHKT_RQ1_to_RQ4]
  auto[FULLY_AUTONOMOUS]
  now --> data --> train --> deploy
  deploy --> paper
  data --> neural
  data --> khkt
  paper --> auto
```

**Đã đi tới:** cuối mũi tên `now` → một phần `data` + toàn bộ **khung** `train/deploy/neural/paper` trong repo + **suite KHKT** (`khkt`) có số liệu.  
**Chưa tới:** artifact train thật trên H200, `active.yaml` desks production, paper campaign có chứng cứ N ngày, RQ1 thắng B0/C1 phổ quát, rồi mới `FULLY_AUTONOMOUS`.

---

### 0.2 Những gì ĐÃ CÓ và không cần làm lại

Các hạng mục dưới đây đã ship trong codebase (runtime / UI / ops), coi như baseline ổn định:

#### A. Runtime & an toàn execution

- Event-driven tick 5 pha, `EventBus`, `TimeEngine`, `TickPipeline`.
- Sentinel equity sync từ `quant.fill` (khớp Cockpit), circuit breaker, VaR.
- Mode-gated isolation: consumer gate + producer air-gap + CCXT air-gap + anomaly gate.
- Execution truthfulness: `LIVE` / `SYNTHETIC` / `DEGRADED` / `OFFLINE` nổi trên health + dashboard (không “giả fill” im lặng khi reject).
- Postgres → SQLite failover có log `CRITICAL`.
- API key/bearer trên mutating routes + audit JSON lines (`logs/econith_audit.log`).
- World mặc định **OFF** (`WORLD_SIMULATION_DEFAULT=false`) để máy yếu không đốt CPU.

#### B. World dạy Quant (nghiên cứu)

- `HypothesisRunner` + schema/generator + sealed rollouts (`rollout_export`, `training/export_world_rollouts.py`).
- API hypotheses (`GET/POST /world/hypotheses*`); `AUTONOMOUS_HYPOTHESIS_IMPLEMENTED=True` (vòng hyp có thật; khác với FULLY_AUTONOMOUS).
- Honesty UI: brains, macro provenance, hypothesis status; Event Log dedupe.
- Hyp/scenario i18n (VI|EN); `HYPOTHESIS_USE_LLM=false` mặc định; LLM (nếu bật) chạy `asyncio.to_thread`.
- Ollama local-first + Groq/OpenRouter fallback (route LLM pool).
- **Nâng cấp hypothesis (gần đây):** `hypothesis_debate.py`, `hypothesis_thinker.py`, `hypothesis_i18n.py`, `operator_voice.py`; locale gate + tests (`tests/test_hypothesis_locale_gate.py`, `tests/test_hypothesis_smart.py`); dashboard locale/theme sync (`LocaleContext`, `LocaleScript`).

#### C. Data / train / deploy **khung** (P0 backlog — code xong)

| Hạng mục | Path / lệnh | Ghi chú |
|----------|-------------|---------|
| Collectors tier | `collectors/market_coin`, `macro_global`, `tradfi_assets` | Deploy VPS 24/7; raw lake `datasets/raw/` |
| Feature-store cut-over | `python -m training.prepare_feature_store` | Raw → `datasets/features` |
| Label multi-symbol-safe | `training/quant/label_symbol.py` (+ `training/label.py` ủy quyền) | Tránh cross-symbol contamination |
| Ingest World rollouts → PPO dataset | `python -m training.ingest_world_rollouts` | Hook sealed JSONL vào bước train; **chưa** auto-deploy |
| Train PPO desks | `training/train_ppo.py` | Chạy trên **H200**, không trên i3 local |
| Train World neural head | `training/train_world.py` → `.pt` | Output vol / bias / premium |
| Deploy + SHA verify | `training/deploy.py` | Ghi `models/registry/active.yaml` |
| **Backtest gate bắt buộc** | `training/deploy_gate.py` trong `--activate` | Fail = không promote; emergency: `--skip-backtest` |
| Predictor load PPO | `ai/agents/agent_loaders.py` | Có `active.yaml` hợp lệ → `agent_brain=trained` |

#### D. Neural World `react()` (P0)

- `NeuralReactionModel` trong `ai/simulator_engine/reaction_models.py`: load ReactionNet `.pt`, map → `Adjustment` (CPI/unrest/growth/rate/yield).
- `default_models()` chỉ **append** neural khi checkpoint load thành công (không còn noop giả “đã neural”).

#### E. Operator truth & hot-path intelligence (P1)

- World UI ↔ backend live set: `dashboard/constants/liveWorld.ts` — **6 hub** `USA/CHN/VNM/JPN/IND/DEU`; mutate chỉ khi `backendLive`; còn lại “topology only”.
- Portfolio sizing trên hot path: `PortfolioRiskModel` trong `econith_quant/bridge/ai_bridge.py` (VaR haircut × meta appetite trước `order.intent`).
- Meta consumers thật:
  - `meta.quant.directive` → AIBridge derisk + Predictor lean direction/action
  - `meta.world.directive` → WorldKernel amplify micro shock
  - `meta.risk.directive` → Sentinel (đã có từ trước)
- Social production-lite: `SocialServiceBridge(bus=...)` + `publish_health_event()` lúc boot; `GRAPH_BACKEND=local` trừ khi có Zep.

#### F. Paper soak & polish (M3 / P2)

- `python -m scripts.paper_soak_check` (+ `--require-demo`) — checklist env + bước ops thủ công N ngày.
- Branding XAI: **weighted feature attribution** (không gọi SHAP giả); `ai/explainability/attribution.py`.
- News ticker UI: `dashboard/components/JournalistTicker.tsx` trên Landing.
- `scripts/calibrate_world.py` đọc panel/features macro (và `datasets/raw/macro/**/*.jsonl` khi có); output chuẩn `models/world/stochastic_coeffs.json` (đã recalibrate RQ2 → Moment L1 4/4 trên EXP_002).
- JSON structured logging: `ECONITH_JSON_LOGS=true` trong bootstrap `main.py`.
- CI: pytest first-party + deploy-gate smoke + paper soak + calibrate (`/.github/workflows/ci.yml`).
- Preset máy yếu gom block rõ trong `.env.example`.

#### G. Kiểm thử

- Suite first-party dưới `tests/` (runtime, execution truthfulness, hierarchical world, hypotheses, backlog M1–M3, …).
- Backlog smoke: `tests/test_backlog_m1_m3.py`.
- Hypothesis: `tests/test_hypothesis_locale_gate.py`, `tests/test_hypothesis_smart.py`, `tests/test_log_i18n.py`.

#### H. Đánh giá khoa học KHKT (RQ1–RQ4) — nâng cấp gần đây

Workspace độc lập `KHKT_Evaluation/` (không thay runtime product claim):

| RQ | Nội dung | Kết quả gần nhất (trung thực) |
|----|----------|-------------------------------|
| **RQ1** | Baseline B0 vs ECONITH E1 vs control C1 trên vol forecast | Protocol **`oos_beta`**: β/lag fit **train-only**, test OOS; metrics thêm Δvol MAE, regime DirAcc, Diebold–Mariano. Full-panel EXP_001: `passes_random_control=False`. Event windows EXP_006: E1>B0 **2/4**, E1>C1 **3/4** |
| **RQ2** | Calibration stochastic World vs empirical macro | Recalibrate (`scripts/calibrate_world.py` + `macro_series.py`): mu khóa mean thực nghiệm, CPI YoY, cùng `dt`/seed. EXP_002: **`calibrated_wins_moment_l1 = 4/4`**. Coeffs: `models/world/stochastic_coeffs.json` (backup `.pre_rq2_recal.json`) |
| **RQ3** | Coupling / ablation World ON–OFF | EXP_003 + EXP_007: khác biệt đo được (measurable scenarios) |
| **RQ4** | Reproducibility | EXP_005 fingerprint **2/2**; EXP_009 `reproducible_rate=1.0` trên **5** lần chạy (trong protocol, không claim tuyệt đối) |

- Suite EXP_001…009 + `run_all.py` + `final_report/` + báo cáo Word `ECONITH_KHKT_BaoCao.docx`.
- Shared eval: `common/vol_eval.py` (oos_beta + legacy), `common/macro_series.py`, panel `dataset_info/macro_calibration_panel.parquet`.
- Nguồn sự thật số liệu: `KHKT_Evaluation/final_report/EXECUTIVE_SUMMARY.md` (không bịa số).

---

### 0.3 Đang đứng ở đâu (milestone cảm nhận)

| Milestone | Kết quả mong muốn | Hiện trạng |
|-----------|-------------------|------------|
| **M1 — Trained Quant** | Main Control `agent_brain=trained` (hoặc mixed); backtest gate chặn promote xấu | **Gate + loaders sẵn**; desk vẫn heuristic cho đến khi H200 train + deploy artifact |
| **M2 — World neural + UI honesty** | `react()` không rỗng khi có `.pt`; globe không overclaim 50 nước | **Code xanh**; neural chỉ “sống” khi có checkpoint train_world |
| **M3 — Ops ready** | Paper soak report N ngày; rồi mới live keys / FULLY_AUTONOMOUS | **Checklist có**; chưa có nhật ký chiến dịch production |
| **R — KHKT scientific eval** | Protocol tái lập RQ1–RQ4 + báo cáo trung thực | **Suite + số liệu có**; RQ2 4/4; RQ1 full-panel chưa thắng B0/C1 |

**Cảm nhận sản phẩm hôm nay:** nghiên cứu / demo / SIM mạnh, honesty UI tốt, **lớp đánh giá KHKT đã chạy được**, đường train→customs đã mở — **chưa** phải “AI Quant đã train sẵn trong box” hay “World cải thiện vol forecast phổ quát”.

---

### 0.4 Việc CẦN làm tiếp (ưu tiên đúng thứ tự)

#### Ngay (đóng claim “trained” + chứng cứ ops)

1. **Chạy collectors ổn định** (VPS hoặc local dài ngày) → đầy `datasets/raw/`.
2. **Cut-over feature store** thật: `prepare_feature_store` → label → holdout parquet.
3. **Train trên H200:** `train_ppo` (+ tùy chọn `train_world`) → `.zip` / `.pt` → `models/`.
4. **Promote có gate:**  
   `python training/deploy.py --activate --holdout ./datasets/processed/quant_holdout.parquet`  
   (không `--skip-backtest` trừ khi hiểu rõ rủi ro).
5. **Restart backend** và xác nhận telemetry `agent_brain=trained` / mixed.
6. **Paper / DEMO soak N ngày:** Sentinel + fills truth + PnL; ghi nhật ký; dùng `paper_soak_check --require-demo` làm gate env.

#### Sau đó (chỉ khi 1–6 xanh)

7. Cân nhắc live REALITY với credential demo/testnet trước, vốn nhỏ.
8. **FULLY_AUTONOMOUS:** implement consumer retrain→backtest→deploy + đặt `AUTONOMOUS_LOOP_IMPLEMENTED=True` + mở UI — **không** bật sớm hơn.
9. H200 orchestrator liên tục + promote có người duyệt.
10. Prometheus/Grafana + giám sát collector.
11. Restructure package theo `docs/RESTRUCTURE_BLUEPRINT.md` nếu scale team.
12. Không ưu tiên vendor archive (ABIDES/Mesa/…) như product path.

#### Việc *không* nên làm ngay

- Train 70B local trên CPU yếu (i3) — đốt quạt, không phải đường product.
- Bật `FULLY_AUTONOMOUS` khi desk còn heuristic / chưa paper soak.
- Live REALITY vốn thật khi chưa có trained desks + soak.

---

### 0.5 Lệnh nhanh cho báo cáo / ops

```powershell
# Checklist DEMO soak (env)
python -m scripts.paper_soak_check --require-demo

# Feature store + label (sau khi có raw)
python -m training.prepare_feature_store --raw ./datasets/raw --out ./datasets/features
python training/label.py --input ./datasets/features

# Rollouts World → dataset (không auto-deploy)
python -m training.export_world_rollouts --dir data/rollouts
python -m training.ingest_world_rollouts --dir data/rollouts

# Deploy (máy có holdout + model đã verify)
python training/deploy.py --activate --holdout ./datasets/processed/quant_holdout.parquet

# Calibrate World (panel BTC+macro → models/world/stochastic_coeffs.json)
python scripts/calibrate_world.py --output ./models/world/stochastic_coeffs.json

# KHKT evaluation (RQ1–RQ4) — offline, tái chạy được
python -m KHKT_Evaluation.run_all
python -m KHKT_Evaluation.final_report.build_final_report

# Tests
python -m pytest tests/ -q --tb=short
```

Preset máy yếu (copy vào `.env`) — xem block **“PRESET êm quạt”** trong `.env.example`. Chi tiết KHKT: `KHKT_Evaluation/README.md`.

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
- `Predictor`, `AIBridge` (portfolio VaR + meta appetite), `QuantExecutionBridge`, `CCXTBinanceBridge`.
- `WorldKernel` (meta.world consumer), `SovereignWorldGraph`, `WorldBridge`, `HypothesisRunner` (khi World ON + mode hyp).
- `SocialServiceBridge` health → EventBus (best-effort lúc boot).
- API security middleware, audit trail, DB failover bootstrap; optional `ECONITH_JSON_LOGS`.

### Những gì đã implement nhưng phụ thuộc artifact / ops bên ngoài

- `collectors/` — sẵn sàng deploy VPS; chưa thay thế 100% mọi nguồn macro runtime.
- `training/prepare_feature_store.py`, `deploy_gate.py`, `ingest_world_rollouts.py` — **khung cut-over đã có**; cần raw data + H200 train để có desk thật.
- `NeuralReactionModel` — code live; cần `.pt` từ `train_world` mới vào stack.
- `scripts/paper_soak_check` — checklist; cần campaign N ngày do người vận hành.

Nói ngắn gọn: **runtime chính chạy được + backlog P0–P2 đã đóng trong code**; **artifact train + paper campaign** vẫn là bước ops tiếp theo (xem §0).

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

### 2.4 Scientific evaluation (KHKT) & World calibration

- **Protocol B0 / E1 / C1**: baseline thị trường thuần, ECONITH có World context, control xáo trộn — so trên cùng panel, không overclaim PnL.
- **`oos_beta` vol eval**: `ŷ_E1 = ŷ_B0 + β·(L(vol_mult)/μ_train − 1)`; β và lag chỉ fit trên train; C1 shuffle **sau** β; giữ `protocol_legacy` multiplicative để so sánh.
- **Calibration RQ2**: `scripts/calibrate_world.py` + `KHKT_Evaluation/common/macro_series.py` (đơn vị thống nhất, inflation YoY); khóa `mu` empirical; refine jumps/theta/sigma theo Moment L1 → `models/world/stochastic_coeffs.json`.
- **Hypothesis smart layer**: debate / thinker / operator voice / i18n; locale prefs đồng bộ dashboard ↔ backend.
- **Báo cáo**: `KHKT_Evaluation/final_report/` + `ECONITH_KHKT_BaoCao.docx` — ghi nhận cả kết quả dương (calibration 4/4, coupling, tái lập) lẫn hỗn hợp (RQ1 full-panel).

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

- `meta.quant.directive` → **AIBridge** (risk appetite / derisk) + **Predictor** (lean direction/action)
- `meta.risk.directive` → **Sentinel**
- `meta.world.directive` → **WorldKernel** (amplify micro shock)
- `meta.context` → read-model / telemetry (advisory)

Portfolio VaR sizing (`ai/quant/portfolio.py` → `PortfolioRiskModel`) đã nằm trên hot path order trong `AIBridge` trước khi phát `order.intent`.

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
| `meta.quant.directive` | `CoreAIOrchestrator` | `AIBridge` (derisk), `Predictor` (signal lean) |
| `meta.risk.directive` | `CoreAIOrchestrator` | `Sentinel` |
| `meta.world.directive` | `CoreAIOrchestrator` | `WorldKernel` (scenario / micro pressure) |
| `meta.context` | `CoreAIOrchestrator` | consolidated read-model / telemetry |
| `journalist.news` | `JournalistLLM` | API + `JournalistTicker` (Landing) |

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

- Hiển thị sovereign snapshot, chronology fork, scenario / hypothesis mutations và world state read-model.
- Globe có **50 node topology** để quan sát; **chỉ 6 hub backend** (`USA/CHN/VNM/JPN/IND/DEU`) được mutate live — UI ghi rõ “LIVE mutate” vs “topology only”.
- World mặc định tắt; khi bật ở `REALITY` vẫn quan sát được nhưng không contaminate execution (isolation 4 tầng).

### Journalist / news

- `JournalistLLM` có terminal data path và API read-model.
- `GET /api/v1/journalist/news` hoạt động.
- UI: `JournalistTicker` trên Landing Page đọc stream / API news.

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
│   └── simulator_engine/             # World kernel + sovereign + hyp debate/thinker/i18n
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
├── KHKT_Evaluation/                  # scientific eval RQ1–RQ4 (EXP_001…009, final_report)
│   ├── common/                       # vol_eval (oos_beta), macro_series, metrics, bridges
│   ├── experiments/                  # baseline, calibration, ablation, events, stats…
│   ├── final_report/                 # EXECUTIVE_SUMMARY + figures/tables
│   └── run_all.py
├── infrastructure/
│   ├── alternative/  daemon/  preprocessing/  storage/  websocket/
├── datasets/
│   ├── raw/                          # append-only raw lake cho collectors
│   ├── processed/                    # labeled / merged training data
│   └── ...                           # features / tensor cache / sqlite tùy workflow
├── models/
│   └── world/stochastic_coeffs.json  # World OU/jump coeffs sau calibrate RQ2
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
- **Đánh giá khoa học** nằm tách trong `KHKT_Evaluation/` — offline, tái chạy được; không phải product trading claim.
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

`training/evaluation/backtest.py` là harness kiểm định offline:

- vectorized, net-of-cost, fee / slippage / spread friction,
- xuất Sharpe, Sortino, Max Drawdown, Profit Factor, Win Rate, Turnover, PnL by Regime.

**Gate deploy:** `training/deploy_gate.py` được gọi khi `python training/deploy.py --activate`. Thiếu holdout/metrics đạt chuẩn → **không** ghi `active.yaml` (trừ `--skip-backtest` khẩn cấp hoặc `--backtest-report` JSON sẵn).

### 10.6 Mount dataset lên RunPod H200

**Hướng dẫn từng bước (checklist đầy đủ):** [`docs/RUNPOD_H200_TRAINING.md`](docs/RUNPOD_H200_TRAINING.md)

Tóm tắt nhanh:

```bash
# Local: audit + pack (~150–250 MB — KHÔNG upload 48GB tick lake)
python -m training.audit_train_ready
python -m training.pack_runpod_bundle
# scp dist/econith_runpod_train_bundle.zip → pod → unzip

cd /workspace/econith
pip install -r requirements-train.txt
python -m training.train_ppo --dataset-profile hf --agent scalper --timesteps 200000
python -m training.train_ppo --dataset-profile klines --agent trend --timesteps 200000
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
# Khuyến nghị (có gate):
python training/deploy.py --activate --holdout ./datasets/processed/quant_holdout.parquet
# Legacy make target (kiểm tra Makefile có forward đủ flag gate hay không trước khi dùng prod):
make model-deploy
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

> Chi tiết ưu tiên + bảng “đã xong / chưa xong” nằm ở **§0 Báo cáo trạng thái**. Dưới đây là backlog còn lại sau khi P0–P2 code đã đóng và suite KHKT đã chạy.

### Đã xong gần đây (không cần làm lại)

- KHKT EXP_001–009 + final report; recalibrate World → Moment L1 **4/4**; protocol `oos_beta` + event windows + DM.
- Hypothesis debate/thinker/locale; calibrate path ghi `models/world/stochastic_coeffs.json`.

### P0 ops (artifact — không phải “viết lại code”)

1. Collectors 24/7 thật → raw lake đầy đủ.
2. Chạy `prepare_feature_store` + label + holdout parquet sạch.
3. Train PPO (+ optional World `.pt`) trên H200 → `models/`.
4. `deploy.py --activate` **có** backtest gate (đã wire trong code).
5. Paper / DEMO soak N ngày có nhật ký PnL / Sentinel / fills.

### P1 hardening (sau artifact)

6. Ghi dataset hash + metrics + code hash vào registry chặt hơn.
7. Quy hoạch `Makefile` front đúng path mới (prepare / ingest / gate) thay vì legacy-only.
8. Mở rộng CI: smoke social sidecar / deploy dry-run / e2e metrics WS (GPU không bắt buộc).

### P2 / P3 — chỉ sau paper + trained desks

9. Bật `FULLY_AUTONOMOUS` khi `AUTONOMOUS_LOOP_IMPLEMENTED=True` + consumer retrain→backtest→deploy.
10. H200 orchestrator liên tục + promote có người duyệt.
11. `AlertDispatcher` → webhook cho `db_failover`, `exchange_degraded`, `sentinel_freeze`, `ws_disconnect`.
12. Prometheus / Grafana / supervision collectors.
13. Restructure theo `docs/RESTRUCTURE_BLUEPRINT.md` nếu cần scale team.

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

### Đã ổn và dùng được (runtime + backlog code)

- Runtime event-driven đầy đủ, deterministic 5-phase tick.
- Quant / World sovereignty có 4 tầng cô lập; World default OFF trên máy yếu.
- Sentinel dùng execution-truth equity, khớp Cockpit 1:1.
- CCXT live/synthetic execution có air-gap và surfacing degradation.
- Database failover không silent-fail; optional JSON logs (`ECONITH_JSON_LOGS`).
- Quant / World / Landing dashboard: mission control, honesty UI, Journalist ticker, live-hub mutate only.
- HypothesisRunner + sealed rollouts + smart debate/thinker/i18n; neural `react()` khi có `.pt`.
- Portfolio VaR + meta directive consumers trên hot path.
- Deploy customs + **backtest gate** trước `active.yaml`.
- Paper soak checklist; CI pytest + gate/soak/calibrate smoke.
- Regression / backlog / hypothesis tests under `tests/`.
- **KHKT_Evaluation**: EXP_001–009, `oos_beta`, calibration Moment L1 **4/4**, coupling, reproducibility protocol; báo cáo `final_report/` + `ECONITH_KHKT_BaoCao.docx`.

### Phụ thuộc ops / cloud (chưa “xong product claim”)

- Collectors chưa phải nguồn data mặc định dài ngày của mọi môi trường.
- Chưa có desk SB3 train sẵn trong repo / `active.yaml` production trên máy local mặc định.
- Paper campaign N ngày chưa thay checklist bằng báo cáo chiến dịch có chữ ký ops.
- `FULLY_AUTONOMOUS` vẫn flag-only.
- AlertDispatcher / Prometheus chưa phải stack giám sát mặc định.
- RQ1 full-panel: E1 **chưa** vượt B0/C1 phổ quát (event windows có tín hiệu cục bộ) — không claim alpha / digital twin.

### Kết luận kỹ thuật ngắn gọn

**Code path P0–P2 của backlog hoàn thiện đã đóng; lớp đánh giá KHKT (RQ1–RQ4) đã có số liệu tái chạy được.**  
Trọng tâm tiếp theo không phải “viết lại kiến trúc”, mà là:

1. chạy collectors thật dài ngày,
2. build processed feature store sạch,
3. label multi-symbol-safe + backtest,
4. train trên H200 + promote qua gate,
5. paper soak có nhật ký,
6. **chỉ sau đó** mới cân nhắc live keys và `FULLY_AUTONOMOUS`.

Xem **§0 Báo cáo trạng thái** làm nguồn sự thật vận hành hàng ngày; xem `KHKT_Evaluation/final_report/EXECUTIVE_SUMMARY.md` cho claim khoa học.
