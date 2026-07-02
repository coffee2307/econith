# ECONITH — Technical Architecture & Operations Manual

> **BẢN CẬP NHẬT XÁC NHẬN:** 17:05, 02/07/2026 (UTC+7)  
> **Trạng thái:** Đã ghi trực tiếp vào `README.md` trong workspace hiện tại.

> **Cập nhật: 07/2026**
>
> - Backend đã chạy ổn định với Binance API thật (`mock: false`).
> - Chế độ mặc định an toàn là `QUANT_MODE=REALITY`.
> - Đã có đầy đủ pipeline huấn luyện A→E trong `training/` + `Makefile`.
> - Đã có **Last Mile Inference**: Predictor tự nạp model từ `models/registry/active.yaml` (hoặc env override), fallback về heuristic nếu thiếu checkpoint.

## Mục lục kỹ thuật (tiếng Việt)

- Giới thiệu dự án: Mục `1`
- Kiến trúc hệ thống: Mục `2`, `3`, `4`, `5`
- Hướng dẫn nhanh (Quick Start): Mục `13`, `14`, `15`
- Quy trình huấn luyện và triển khai: Mục `8`, `9`, `12`, `19`
- Cơ chế vận hành hậu trường (3 chuyên gia + Fusion): Mục `10`, `11`
- Lộ trình phát triển (Next Steps): Mục `16`

---

## 1. Mục tiêu dự án

**ECONITH** là dự án nghiên cứu cá nhân kết hợp hai hệ:

- **ECONITH Quant**: hệ thống AI phân tích/tạo tín hiệu giao dịch có kiểm soát rủi ro bằng Sentinel.
- **ECONITH World**: mô phỏng kinh tế-vĩ mô-địa chính trị để stress-test, nghiên cứu cơ chế lan truyền tác động.

Mục tiêu cốt lõi là đi trọn vòng đời mô hình định lượng:

`Dữ liệu thật -> Feature -> Label -> Train -> Verify -> Activate -> Inference live`

### Giá trị KHKT của đề tài

| Trục đánh giá | ECONITH hiện có | Ý nghĩa đối với giám khảo KHKT |
|---|---|---|
| Tính hệ thống | Tick Engine 5 pha + Event Bus + Sentinel | Chứng minh thiết kế có kiến trúc, không phải script rời rạc |
| Tính AI | PPO đa tác tử + HMM/GMM regime + World model | Có chiều sâu mô hình và tư duy tổ hợp |
| Tính thực nghiệm | Pipeline A→E + holdout + early-stop + checksum | Có quy trình kiểm chứng khoa học, có thể lặp lại |
| Tính an toàn | REALITY/SIMULATION sovereignty + rollback | Hạn chế rủi ro vận hành và sai lệch thực nghiệm |

### Độ mạnh hiện tại của hệ thống

- **Điểm mạnh:** đã có full stack từ dữ liệu thật đến model active trên runtime.
- **Độ tin cậy kỹ thuật:** có cơ chế kiểm định trước triển khai (`model-verify`) và rollback.
- **Độ sẵn sàng nghiên cứu:** đủ để trình diễn cả “xây mô hình” và “vận hành mô hình” trong cùng hệ.

---

## 2. Kiến trúc tổng quan

```text
econith/
├── ai/
│   ├── agents/                      # Agent logic + model loaders
│   ├── inference/predictor.py       # Boardroom inference, phát ai.signal
│   ├── regime/                      # classifier/switcher/regime loader
│   ├── reward/                      # anti-greed reward
│   └── simulator_engine/            # World kernel + causal graph
├── core/
│   ├── engine.py                    # Deterministic Tick Engine (5 phase)
│   ├── event_bus.py                 # Event spine
│   ├── mode.py                      # REALITY / SIMULATION
│   └── telemetry.py                 # Snapshot cho dashboard
├── infrastructure/
│   ├── websocket/streamer.py        # Binance stream (LIVE/MOCK)
│   ├── preprocessing/pipeline.py    # OBI + Volume Delta
│   ├── alternative/provider.py      # Funding/OI/Liquidations
│   └── feature_store/               # FeatureBuilder/Writer/Loader
├── training/
│   ├── collect.py                   # Phase A: data collection
│   ├── label.py                     # Phase B: labeling
│   ├── train_ppo.py                 # PPO training
│   ├── fit_regime.py                # HMM/GMM training
│   ├── train_world.py               # Neural world model
│   ├── early_stop.py                # quality inspector
│   ├── orchestrator.py              # Phase C/D orchestration
│   └── deploy.py                    # Phase E: verify/activate/rollback
├── datasets/
│   ├── raw/
│   ├── features/
│   └── processed/
├── models/
│   ├── agents/
│   ├── regime/
│   ├── world/
│   └── registry/
├── Makefile
└── requirements-train.txt
```

---

## 3. Cơ chế vận hành online

Luồng chạy runtime:

1. `BinanceWebSocketStreamer` phát `md.aggTrade`, `md.depth`.
2. `MarketDataPipeline` xử lý và phát:
   - `md.ticker`
   - `indicator.obi`
   - `indicator.volume_delta`
3. `AlternativeDataProvider` cập nhật:
   - `alt.funding_rate`
   - `alt.open_interest`
   - `alt.liquidation`
4. `Predictor` lấy feature live -> phân loại regime -> lấy tín hiệu từ 3 agents -> fusion -> phát `ai.signal`.
5. `AIBridge`/execution nhận tín hiệu.
6. `Sentinel` có quyền veto cuối cùng.
7. Dashboard đọc snapshot qua WS `/api/v1/stream/metrics`.

### Sơ đồ luồng dữ liệu thực tế

```text
Binance Stream + Alt Data
        │
        ▼
[Feature Pipeline]
  - OBI
  - Volume Delta
  - Funding/OI/Liquidation
        │
        ▼
[Predictor Boardroom]
  - Regime Classifier
  - 3 AI Desks (Trend / Mean Reversion / Scalper)
        │
        ▼
[Regime-weighted Fusion]
        │
        ▼
[Sentinel Risk Gate] ──> veto/allow
        │
        ▼
     ai.signal
```

### Ẩn dụ kinh tế để hiểu nhanh

- Hệ thống giống một “hội đồng đầu tư”: mỗi chuyên gia có góc nhìn khác nhau.
- Regime giống “bối cảnh thị trường” (lạm phát cao, biến động mạnh, đi ngang).
- Fusion giống “bỏ phiếu có trọng số” theo bối cảnh, không ai luôn đúng mọi lúc.

---

## 4. Deterministic Tick Engine (5 phase)

`core/engine.py` chạy 5 phase cố định theo thứ tự:

1. `SNAPSHOT`
2. `APPLY_EVENTS`
3. `RESOLVE_CONFLICTS`
4. `UPDATE_WORLD`
5. `EMIT_SIGNALS`

Ưu điểm:

- Dễ kiểm chứng logic từng phase.
- Tránh race condition giữa các bước.
- Rõ cơ chế conflict/veto (đặc biệt khi Sentinel can thiệp).

---

## 5. REALITY vs SIMULATION

`core/mode.py`:

- **REALITY** (mặc định):
  - Chỉ dùng dữ liệu thật.
  - Chặn `world.micro_impact` vào Quant.
  - Khóa anomaly injection.
- **SIMULATION**:
  - Mở coupling World->Quant để nghiên cứu kịch bản.
  - Cho phép inject anomaly để stress-test.

### Health check kỳ vọng khi chạy an toàn

```json
{
  "status": "ok",
  "service": "backend_core",
  "mock": false,
  "quant_mode": {
    "mode": "REALITY",
    "coupling_enabled": false,
    "anomaly_injection_enabled": false
  }
}
```

---

## 6. Các mô hình AI cần huấn luyện

Pipeline hiện tại tạo 5 artifact chính:

1. `models/agents/trend_ppo.zip`
2. `models/agents/mean_reversion_ppo.zip`
3. `models/agents/scalper_ppo.zip`
4. `models/regime/hmm_4state.pkl` (fallback GMM nếu thiếu `hmmlearn`)
5. `models/world/neural_reaction.pt`

Metadata đi kèm:

- `*.norm.json` cho PPO (đảm bảo train/infer cùng chuẩn hóa)
- `*.metrics.json` / `*.meta.json`
- `models/registry/manifest.yaml` (checksum + metadata)
- `models/registry/active.yaml` (bản active)
- `models/registry/history/` (rollback)

---

## 7. Chuẩn feature schema (train/infer đồng nhất)

Feature row từ pipeline:

- `symbol`, `price`, `mid`, `best_bid`, `best_ask`
- `obi`, `bid_volume`, `ask_volume`, `volume_delta`, `buy_volume`, `sell_volume`, `trade_count`
- `funding_rate`, `time_to_funding_s`, `open_interest`, `oi_change_pct`, `liquidation_notional`
- `effective_buy`, `effective_sell`
- `ts_ms` (được thêm trong collector)

Canonical feature list cho PPO desks (`PPO_FEATURE_COLS`):

- `obi`, `volume_delta`, `buy_volume`, `sell_volume`, `trade_count`
- `funding_rate`, `time_to_funding_s`, `open_interest`, `oi_change_pct`, `liquidation_notional`

---

## 8. Lấy dữ liệu (Phase A)

### 8.1 Collect live

```bash
make data-collect
```

Tương đương:

```bash
python training/collect.py \
  --symbol BTCUSDT \
  --output ./datasets/features \
  --batch-size 500 \
  --duration 0
```

- `duration=0` nghĩa là chạy đến khi dừng tay.
- Output theo lô: `datasets/features/features_XXXXX.parquet`.

### 8.2 Backfill lịch sử OHLCV

```bash
make data-collect-backfill
```

Tương đương:

```bash
python training/collect.py --backfill \
  --symbol BTCUSDT \
  --start 2024-01-01 \
  --end 2025-12-31 \
  --intervals 1m,5m \
  --output ./datasets/raw/binance
```

---

## 9. Pipeline huấn luyện A→E

Pipeline được thiết kế như một **dây chuyền sản xuất khép kín**:

`Mỏ dữ liệu -> Nhà máy tinh luyện nhãn -> Xưởng huấn luyện -> Cổng kiểm định -> Sàn vận hành`

### Phase B — Labeling

```bash
make data-label
```

`training/label.py` thực hiện:

- Tính `forward_return_1m`, `forward_return_5m`, `forward_return_15m`.
- Tính `reward` bằng `breakdown_reward()` anti-greed.
- Chia dữ liệu theo thời gian 80/20 (không shuffle):
  - `datasets/processed/quant_labeled.parquet`
  - `datasets/processed/quant_holdout.parquet`

### Phase C/D — Train + validation

```bash
make setup-train
make train-all
```

`training/orchestrator.py`:

- Lập lịch job theo wave:
  - **Wave 0**: `trend`, `mean_reversion`, `scalper` + `hmm`
  - **Wave 1**: `world_neural`
- Hỗ trợ backend:
  - `multiprocessing` (mặc định)
  - `ray`
- Quản lý song song bằng `--max-gpu-concurrent`.
- Sinh `models/registry/manifest.yaml` sau khi train.
- Theo dõi holdout loss qua `training/early_stop.py` để dừng sớm khi mô hình bắt đầu “học vẹt”.

### Phase E — Verify + activate

```bash
make model-verify
make model-deploy
```

`training/deploy.py`:

- Verify SHA256 từng model theo manifest.
- Nếu pass thì cập nhật `active.yaml`.
- Lưu lịch sử vào `registry/history/`.

Rollback:

```bash
python training/deploy.py --target ./models --rollback
```

### Bảng dây chuyền huấn luyện - triển khai

| Công đoạn | Tệp chính | Đầu vào | Đầu ra | Lệnh Makefile |
|---|---|---|---|---|
| Thu thập dữ liệu | `training/collect.py` | Stream/Backfill Binance | `datasets/features/*.parquet` | `make data-collect`, `make data-collect-backfill` |
| Gắn nhãn | `training/label.py` | Feature parquet | `quant_labeled.parquet`, `quant_holdout.parquet` | `make data-label` |
| Huấn luyện song song | `training/orchestrator.py` + workers | Labeled/Holdout | PPO/HMM/World checkpoints + `manifest.yaml` | `make train-all` |
| Kiểm định | `training/deploy.py` | `manifest.yaml` + checkpoints | Trạng thái pass/fail checksum | `make model-verify` |
| Kích hoạt | `training/deploy.py` | Artifact đã verify | `active.yaml` + history rollback | `make model-deploy` |

---

## 10. Last Mile Inference (đưa “bộ não” lên sàn)

### 10.1 Trading desks

`ai/agents/agent_loaders.py`:

- Nạp checkpoint PPO từ:
  1. `models/registry/active.yaml`
  2. hoặc env override (`TREND_CHECKPOINT`, `MEAN_REV_CHECKPOINT`, `SCALPER_CHECKPOINT`)
- Nạp sidecar normalize (`*.norm.json`).
- Nếu thiếu checkpoint hoặc thiếu SB3/torch -> desk offline -> predictor fallback heuristic.

### 10.2 Regime forecaster

`ai/regime/regime.py`:

- Nạp HMM/GMM bundle từ active registry.
- Classify regime live theo feature vi mô.
- Nếu thiếu bundle -> fallback heuristic classifier.

### 10.3 Predictor boardroom

`ai/inference/predictor.py`:

- Tự động seat trained desks + trained regime nếu có.
- Duy trì fallback an toàn nếu model chưa sẵn sàng.
- `ai.signal` có thêm:
  - `agent_brain`: `trained | heuristic`
  - `regime_brain`: `trained | heuristic`

### Cơ chế “3 chuyên gia” trong thực tế

| Chuyên gia AI | Vai trò | Khi nào được tăng trọng số |
|---|---|---|
| `trend` | Theo xu hướng chính | Khi thị trường có đà rõ (`TRENDING`) |
| `mean_reversion` | Bắt nhịp hồi về cân bằng | Khi thị trường dao động quanh vùng giá (`MEAN_REVERTING`) |
| `scalper` | Phản ứng nhanh với nhiễu ngắn hạn | Khi biến động cao (`VOLATILE`) |

---

## 11. Fusion/Ensemble theo regime

`ai/regime/switcher.py`:

- `TRENDING`: `trend=0.70`, `mean_reversion=0.05`, `scalper=0.25`
- `MEAN_REVERTING`: `trend=0.10`, `mean_reversion=0.70`, `scalper=0.20`
- `VOLATILE`: `trend=0.20`, `mean_reversion=0.20`, `scalper=0.60`
- `CALM`: `trend=0.34`, `mean_reversion=0.33`, `scalper=0.33`

Fusion tạo quyết định cuối cùng (`LONG`/`SHORT`/`FLAT`) với confidence.

### Giải thích fusion theo tư duy đời sống

- Nếu thị trường đang “có trend”, giống như nền kinh tế đang mở rộng mạnh, ý kiến chuyên gia trend được ưu tiên.
- Nếu thị trường “đi ngang”, giống giai đoạn cung-cầu quay về cân bằng, chuyên gia mean-reversion có tiếng nói lớn hơn.
- Nếu thị trường “nhiễu mạnh”, chuyên gia scalper được tăng quyền vì phản ứng nhanh với biến động ngắn.
- Vì vậy, fusion là cơ chế phân bổ “ngân sách niềm tin” theo bối cảnh, thay vì dùng một mô hình cho mọi tình huống.

---

## 12. Makefile commands (chuẩn hiện tại)

Các command chính:

- `make setup-train`
- `make data-collect`
- `make data-collect-backfill`
- `make data-label`
- `make train-all`
- `make train-single JOB=trend`
- `make model-verify`
- `make model-deploy`

Ví dụ override:

```bash
make train-all BACKEND=ray JOBS=trend,mean_reversion,scalper,hmm,world_neural
```

---

## 13. Thiết lập môi trường

### 13.1 Runtime `.env`

```env
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8000

QUANT_MODE=REALITY
MODEL_DIR=./models
MODEL_REGISTRY=./models/registry

BINANCE_DATA_API_KEY=...
BINANCE_DATA_API_SECRET=...
BINANCE_TRADE_API_KEY=...
BINANCE_TRADE_API_SECRET=...

# fallback legacy (vẫn support)
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
```

### 13.2 Optional checkpoint override

```env
TREND_CHECKPOINT=models/agents/trend_ppo.zip
MEAN_REV_CHECKPOINT=models/agents/mean_reversion_ppo.zip
SCALPER_CHECKPOINT=models/agents/scalper_ppo.zip
REGIME_CHECKPOINT=models/regime/hmm_4state.pkl
WORLD_CHECKPOINT=models/world/neural_reaction.pt
```

---

## 14. Khởi chạy nhanh

### Quick Start (Dev trên máy cá nhân)

| Bước | Mục tiêu | Lệnh |
|---|---|---|
| 1 | Cài runtime | `pip install -r requirements.txt` |
| 2 | Cài phụ thuộc train | `pip install -r requirements-train.txt` |
| 3 | Chạy backend | `uvicorn main:app --host 0.0.0.0 --port 8000 --reload` |
| 4 | Chạy dashboard | `cd dashboard && npm install && npm run dev:http` |
| 5 | Kiểm tra health | `curl http://localhost:8000/api/v1/health` |
| 6 | Thu dữ liệu | `make data-collect` |
| 7 | Huấn luyện + kích hoạt | `make data-label && make train-all && make model-deploy` |

### Backend

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Dashboard

```bash
cd dashboard
npm install
npm run dev:http
```

### Kiểm tra health

```bash
curl http://localhost:8000/api/v1/health
```

Kỳ vọng:

- `status = ok`
- `mock = false` (nếu key Binance hợp lệ)
- `quant_mode.mode = REALITY`

---

## 15. Hướng dẫn RunPod H200

### Quy trình A→Z trên RunPod H200

| Bước | Mục tiêu | Lệnh |
|---|---|---|
| A | Chuẩn bị môi trường | `cd /workspace/econith` |
| B | Cài phụ thuộc train | `make setup-train` |
| C | Label dữ liệu | `make data-label` |
| D | Huấn luyện song song | `make train-all BACKEND=multiprocessing` |
| E | Kiểm định artifact | `make model-verify` |
| F | Kích hoạt model | `make model-deploy` |
| G | Khởi động backend nạp model mới | `uvicorn main:app --host 0.0.0.0 --port 8000 --reload` |

```bash
# trên pod
cd /workspace/econith
make setup-train
make train-all BACKEND=multiprocessing
# hoặc
make train-all BACKEND=ray
```

Khuyến nghị thực tế:

- Bắt đầu bằng `multiprocessing` cho 1 H200.
- Chuyển qua `ray` khi cần scale nhiều node.
- Theo dõi log để điều chỉnh `max-gpu-concurrent`.

---

## 16. “Có nhà nhưng thiếu bộ não” — roadmap tiếp theo

Hiện tại dự án đã có:

- **Nhà**: core engine, data plane, dashboard, Sentinel, mode sovereignty.
- **Xưởng đào tạo**: đầy đủ pipeline training + deploy gate.
- **Đường ra sàn**: model loader + active registry + rollback.

Các bước tiếp theo ưu tiên:

1. Tăng dữ liệu chất lượng (collect dài ngày, liên tục).
2. Thiết lập chu kỳ huấn luyện (daily nhẹ + weekly sâu).
3. Chuẩn hóa benchmark trước activate (holdout, drawdown, turnover, stability).
4. Chạy paper trading testnet đủ dài trước khi dùng vốn thật.
5. Thêm panel dashboard cho `agent_brain`, `regime_brain`, holdout metrics.

### Next Steps đề xuất cho đề tài KHKT

1. **Visualizing Model Confidence**
   - Vẽ confidence theo thời gian của từng desk và confidence fusion tổng.
   - Mục tiêu: chứng minh hệ thống “biết khi nào nên tự tin, khi nào nên phòng thủ”.
2. **Backtesting Comparison Dashboard**
   - So sánh `trained` vs `heuristic` trên cùng giai đoạn dữ liệu.
   - Báo cáo các chỉ số: hit-rate, drawdown, turnover, stability.
3. **Stress Testing với Black Swan Scenarios**
   - Tạo kịch bản shock thanh khoản, spike funding, liquidation cascade trong `SIMULATION`.
   - Đánh giá khả năng hệ thống giảm đòn bẩy/rút về `FLAT`.
4. **Model Governance nâng cao**
   - Gắn version, commit hash, tập dữ liệu train vào manifest để audit truy xuất đầy đủ.
5. **Đánh giá đa tài sản**
   - Mở rộng từ 1 symbol sang nhiều symbol để đánh giá độ bền của kiến trúc fusion.

---

## 17. Bảo mật bắt buộc

- Không commit `.env`.
- Không chụp/chia sẻ ảnh chứa API key/secret.
- Nếu nghi lộ key: revoke ngay và tạo key mới.

---

## 18. Tệp mã nguồn tham chiếu quan trọng

- `core/engine.py` — Tick Engine 5 phase
- `core/mode.py` — REALITY/SIMULATION gates
- `main.py` — API runtime + health
- `training/collect.py` — Phase A
- `training/label.py` — Phase B
- `training/train_ppo.py` — PPO + normalizer sidecar
- `training/fit_regime.py` — HMM/GMM
- `training/train_world.py` — neural world model
- `training/orchestrator.py` — orchestration + manifest
- `training/deploy.py` — verify/activate/rollback
- `ai/agents/agent_loaders.py` — live trading desks
- `ai/regime/regime.py` — live trained regime loader
- `ai/inference/predictor.py` — executive inference boardroom

---

## 19. Chuỗi lệnh vận hành đề xuất

```bash
# 1) Thu thập dữ liệu
make data-collect

# 2) Label + huấn luyện
make data-label
make setup-train
make train-all

# 3) Kiểm định + kích hoạt
make model-verify
make model-deploy

# 4) Restart backend để nạp active models
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Sau restart, kiểm tra telemetry/`ai.signal`:

- `agent_brain` nên chuyển sang `trained` khi PPO checkpoint và runtime stack hợp lệ.
- `regime_brain` nên là `trained` khi `hmm_4state.pkl` active và load thành công.

---

ECONITH hiện đã có đủ khung để đi trọn vòng đời mô hình:
**dữ liệu thật -> huấn luyện -> kiểm định -> kích hoạt -> suy luận live an toàn**.
