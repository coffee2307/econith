# ECONITH — RunPod H200 Training Guide

Train PPO desks on a rented NVIDIA H200. Do **not** upload the ~48GB tick lake.
Upload only labeled parquets + code (~150–250 MB).

---

## 0. Readiness check (local PC, before renting)

```powershell
cd F:\econith
python -m training.audit_train_ready
python -m training.pack_runpod_bundle
```

Expect:

| Artifact | Track | Role |
|----------|-------|------|
| `datasets/processed/quant_labeled.parquet` | klines / swing | ~2.1M rows, 10 symbols |
| `datasets/processed/quant_holdout.parquet` | klines | sealed exam |
| `datasets/processed/quant_labeled_hf.parquet` | HF / scalp | BTC (expand ETH later) |
| `datasets/processed/quant_holdout_hf.parquet` | HF | sealed exam |
| `dist/econith_runpod_train_bundle.zip` | both | upload this |

**Verdict rules**

- `READY` = labeled + holdout exist, core cols (`ts_ms`, `symbol`, `reward`) present, every agent can resolve a `forward_return_*` target.
- Klines missing HF microstructure cols (`spread`, `vpin`, …) is **OK** — trainer uses column intersection; `norm.json` stores what was used.
- HF currently thin if only 1 day / 1 symbol — still trains; expand to 3–7 days BTC+ETH locally when you can.

---

## 1. Create the RunPod pod

1. Open [RunPod](https://www.runpod.io/) → **Pods** → **Deploy**.
2. GPU: **H200** (1× is enough for PPO; 8× only if you use `torchrun` + `training.h200`).
3. Template: **PyTorch** (CUDA 12.x) or **RunPod Pytorch**.
4. Container disk: **≥ 50 GB** (code + data + checkpoints).
5. Volume (optional Network Volume): attach if you will re-use pods.
6. Expose: SSH + Jupyter/HTTP if you like a browser terminal.
7. Deploy → wait until **Running** → copy **SSH** command.

---

## 2. Upload the bundle

### Option A — SCP (simplest)

On your Windows PC (PowerShell), replace `IP` / `PORT` / key from RunPod:

```powershell
cd F:\econith
scp -P PORT dist\econith_runpod_train_bundle.zip root@IP:/workspace/
```

On the pod:

```bash
cd /workspace
unzip -o econith_runpod_train_bundle.zip -d econith
cd econith
ls datasets/processed
```

### Option B — Network volume / object storage

Upload zip to S3/R2 → on pod `wget` / `rclone copy` → unzip same as above.

### Do **not** upload

- `datasets/training_lake/market/crypto_*` (tens of GB ticks)
- `archive/vendors/`, `econith_social/`, raw feature shards unless debugging

---

## 3. Install deps on the pod

```bash
cd /workspace/econith
python -V   # prefer 3.10–3.12

# Torch is usually already in the RunPod image — check CUDA first:
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"

pip install -U pip
pip install -r requirements-train.txt

# If pip reinstalls a CPU torch over the CUDA one, reinstall CUDA torch for your image, e.g.:
# pip install --force-reinstall torch --index-url https://download.pytorch.org/whl/cu124
```

Quick smoke:

```bash
python -m training.audit_train_ready
nvidia-smi
```

---

## 4. Train (recommended commands)

### A. HF scalp track (microstructure) — start here for scalper

```bash
cd /workspace/econith

python -m training.train_ppo \
  --dataset-profile hf \
  --agent scalper \
  --timesteps 200000 \
  --patience 5 \
  --eval-freq 10000 \
  --output ./models/agents/scalper_hf_ppo.zip
```

Then mean-reversion on the same HF set:

```bash
python -m training.train_ppo \
  --dataset-profile hf \
  --agent mean_reversion \
  --timesteps 200000 \
  --output ./models/agents/mean_reversion_hf_ppo.zip
```

### B. Klines swing track — trend / longer horizons

```bash
python -m training.train_ppo \
  --dataset-profile klines \
  --agent trend \
  --timesteps 200000 \
  --output ./models/agents/trend_klines_ppo.zip
```

### C. Foreman (all PPO jobs for one profile)

```bash
# Swing desks
python -m training.orchestrator \
  --dataset-profile klines \
  --jobs trend,mean_reversion,scalper,hmm \
  --timesteps 200000 \
  --early-stop-patience 5 \
  --max-gpu-concurrent 3

# HF desks (skip world unless you have rollouts)
python -m training.orchestrator \
  --dataset-profile hf \
  --jobs trend,mean_reversion,scalper \
  --timesteps 200000 \
  --max-gpu-concurrent 3
```

Outputs land under `models/agents/*_{klines|hf}_ppo.zip` plus `.norm.json` and `.metrics.json`.

---

## 5. Verify GPU is actually used

While training:

```bash
watch -n 1 nvidia-smi
```

You should see Python process with non-zero GPU memory. Log line includes `device=auto` via SB3; on H200 it should pick `cuda`.

---

## 6. Download checkpoints back to PC

```powershell
# on PC
scp -P PORT root@IP:/workspace/econith/models/agents/*.zip F:\econith\models\agents\
scp -P PORT root@IP:/workspace/econith/models/agents/*.norm.json F:\econith\models\agents\
scp -P PORT root@IP:/workspace/econith/models/registry/manifest.yaml F:\econith\models\registry\
```

---

## 7. Deploy gate on PC (or on pod)

```powershell
cd F:\econith
python training/deploy.py --activate `
  --holdout ./datasets/processed/quant_holdout_hf.parquet
# or klines:
# python training/deploy.py --activate --holdout ./datasets/processed/quant_holdout.parquet
```

Do **not** use `--skip-backtest` unless emergency. Gate writes `models/registry/active.yaml` only when metrics pass.

---

## 8. After train — paper soak

1. Point env / `active.yaml` at new desks.
2. Run SIM/DEMO for several days.
3. Only then consider live / `FULLY_AUTONOMOUS`.

---

## Cost / time tips

| Item | Guidance |
|------|----------|
| Pod idle | Stop/terminate when not training — H200 is expensive idle |
| First run | 50k–100k timesteps smoke, then 200k–500k |
| HF quality | Prefer 3–7 day BTC+ETH labeled over multi-year ticks |
| Upload | Bundle zip only; rebuild features on PC, not on the pod |
| Multi-GPU | Single H200 is enough for SB3 PPO; use `training.h200` only for custom DDP |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `training data not found` | Unzip so `datasets/processed/*.parquet` sits under `/workspace/econith` |
| `no forward_return_*` | Wrong profile / corrupt parquet — re-label locally |
| CUDA false | Reinstall CUDA torch matching the image |
| OOM | Lower `--timesteps` batch via editing `batch_size` in `train_ppo` or train one agent at a time |
| Gate refuses deploy | Inspect holdout metrics; improve model or keep heuristic desks |

---

## Related

- Local audit: `python -m training.audit_train_ready`
- Pack: `python -m training.pack_runpod_bundle`
- Profiles: `--dataset-profile klines` \| `hf` in `training/train_ppo.py`
