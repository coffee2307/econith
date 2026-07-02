# ============================================================================
#  ECONITH :: Makefile  --  one-word entry points for the whole model factory
# ----------------------------------------------------------------------------
#  Economic analogy: this file is the "control panel" of the factory. Each label
#  (data-collect, train-all, model-deploy...) is a big button that runs a whole
#  department for you, so you never have to remember the long raw commands.
#
#  Usage:
#     make setup-train           # install the training toolbox
#     make data-collect          # PHASE A: mine live data from Binance
#     make data-collect-backfill # PHASE A: buy the historical "price history book"
#     make data-label            # PHASE B: grade the data (returns + reward)
#     make train-all             # PHASE C/D: run all model smelters in parallel
#     make train-single JOB=trend
#     make model-deploy          # PHASE E: ship trained models to production
#     make model-verify          # checksum audit of the model registry
#
#  Windows note: install "make" (e.g. `choco install make`) or just run the
#  underlying `python training/...` commands shown in each recipe by hand.
# ============================================================================

# --- knobs you can override on the command line: `make data-collect SYMBOL=ETHUSDT`
PYTHON      ?= python
PIP         ?= pip
SYMBOL      ?= BTCUSDT

DATA_ROOT   ?= ./datasets
FEATURE_DIR ?= $(DATA_ROOT)/features
RAW_DIR     ?= $(DATA_ROOT)/raw/binance
PROCESSED   ?= $(DATA_ROOT)/processed

MODEL_DIR   ?= ./models
REGISTRY    ?= $(MODEL_DIR)/registry

BATCH_SIZE  ?= 500
DURATION    ?= 0
INTERVALS   ?= 1m,5m
START       ?= 2024-01-01
END         ?= 2025-12-31
HOLDOUT     ?= 0.20
BACKEND     ?= multiprocessing
JOBS        ?= trend,mean_reversion,scalper,hmm,world_neural
PATIENCE    ?= 5

.PHONY: help setup-train data-collect data-collect-backfill data-label \
        train-all train-single model-deploy model-verify clean-features

help:
	@echo "ECONITH factory control panel"
	@echo "  setup-train            install training dependencies"
	@echo "  data-collect           PHASE A  live market collection -> $(FEATURE_DIR)"
	@echo "  data-collect-backfill  PHASE A  historical OHLCV -> $(RAW_DIR)"
	@echo "  data-label             PHASE B  forward returns + anti-greed reward"
	@echo "  train-all              PHASE C/D  parallel PPO/HMM/world training"
	@echo "  train-single JOB=trend PHASE C  train one model"
	@echo "  model-deploy           PHASE E  hot-swap checkpoints into $(MODEL_DIR)"
	@echo "  model-verify           audit SHA256 checksums in the registry"

# --- PHASE A : mine the raw material ----------------------------------------
setup-train:
	$(PIP) install -r requirements-train.txt

data-collect:
	$(PYTHON) training/collect.py \
		--symbol $(SYMBOL) \
		--output $(FEATURE_DIR) \
		--batch-size $(BATCH_SIZE) \
		--duration $(DURATION)

data-collect-backfill:
	$(PYTHON) training/collect.py --backfill \
		--symbol $(SYMBOL) \
		--start $(START) --end $(END) \
		--intervals $(INTERVALS) \
		--output $(RAW_DIR)

# --- PHASE B : grade the ore (labels + reward) ------------------------------
data-label:
	$(PYTHON) training/label.py \
		--input $(FEATURE_DIR) \
		--output $(PROCESSED)/quant_labeled.parquet \
		--holdout-ratio $(HOLDOUT)

# --- PHASE C/D : run the smelters in parallel -------------------------------
train-all: data-label
	$(PYTHON) training/orchestrator.py \
		--data $(PROCESSED) \
		--output $(MODEL_DIR) \
		--backend $(BACKEND) \
		--jobs $(JOBS) \
		--early-stop-patience $(PATIENCE) \
		--holdout $(PROCESSED)/quant_holdout.parquet

train-single:
	@[ -n "$(JOB)" ] || (echo "Usage: make train-single JOB=trend" && exit 1)
	$(PYTHON) training/orchestrator.py \
		--data $(PROCESSED) \
		--output $(MODEL_DIR) \
		--backend $(BACKEND) \
		--jobs $(JOB) \
		--early-stop-patience $(PATIENCE) \
		--holdout $(PROCESSED)/quant_holdout.parquet

# --- PHASE E : ship the finished goods --------------------------------------
model-deploy:
	$(PYTHON) training/deploy.py \
		--registry $(REGISTRY)/manifest.yaml \
		--target $(MODEL_DIR) \
		--activate

model-verify:
	$(PYTHON) training/deploy.py \
		--registry $(REGISTRY)/manifest.yaml \
		--verify-only

clean-features:
	@echo "removing collected feature partitions in $(FEATURE_DIR)"
	rm -f $(FEATURE_DIR)/features_*.parquet
