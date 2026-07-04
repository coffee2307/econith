# ============================================================================
#  ECONITH :: Makefile  --  one-word entry points for the data + model factory
# ----------------------------------------------------------------------------
#  This control panel now fronts the institutional 4-tier data pipeline:
#
#     collectors/  (raw lake)  ->  feature_pipeline (glue)  ->  label_symbol
#                              ->  backtest  ->  H200 training  ->  registry
#
#  Primary workflow (new institutional path):
#     make data-pipeline     # PHASE A  run the 3 standalone collectors 24/7
#     make data-glue         # PHASE A.5 time-align raw lake -> feature store
#     make data-label        # PHASE B  multi-symbol-safe forward-return labels
#     make backtest-baseline # PHASE B.5 sanity-check the labeled set offline
#     make train-all         # PHASE C/D parallel model training
#     make model-deploy      # PHASE E  activate trained checkpoints
#
#  Windows note: `make` is not native. Either install it (`choco install make`)
#  or run the underlying `python -m ...` commands shown in each recipe by hand.
#  The concurrent `data-pipeline` recipe uses a POSIX shell (`&` + `wait`); on
#  Windows launch each collector in its own terminal instead.
# ============================================================================

# --- knobs (override on the CLI, e.g. `make data-glue OUT=datasets/features`)
PYTHON      ?= python
PIP         ?= pip
SYMBOL      ?= BTCUSDT
SYMBOLS     ?=

DATA_ROOT   ?= ./datasets
RAW_LAKE    ?= $(DATA_ROOT)/raw
FEATURE_DIR ?= $(DATA_ROOT)/features
PROCESSED   ?= $(DATA_ROOT)/processed

MODEL_DIR   ?= ./models
REGISTRY    ?= $(MODEL_DIR)/registry

HOLDOUT     ?= 0.20
BACKEND     ?= multiprocessing
JOBS        ?= trend,mean_reversion,scalper,hmm,world_neural
PATIENCE    ?= 5
BASELINE    ?= momentum

.PHONY: help setup-train setup-collect data-pipeline data-glue data-label \
        backtest-baseline train-all train-single model-deploy model-verify \
        clean-features

help:
	@echo "ECONITH factory control panel"
	@echo "  setup-train            install training/runtime dependencies"
	@echo "  setup-collect          install the lightweight collector dependencies"
	@echo "  data-pipeline          PHASE A    run all 3 collectors concurrently (VPS 24/7)"
	@echo "  data-glue              PHASE A.5  time-align raw lake -> $(FEATURE_DIR)"
	@echo "  data-label             PHASE B    multi-symbol-safe labels -> $(PROCESSED)"
	@echo "  backtest-baseline      PHASE B.5  offline metric verification on labels"
	@echo "  train-all              PHASE C/D  parallel model training"
	@echo "  train-single JOB=trend PHASE C    train one model"
	@echo "  model-deploy           PHASE E    activate trained checkpoints"
	@echo "  model-verify           audit SHA256 checksums in the registry"

# --- setup -------------------------------------------------------------------
setup-train:
	$(PIP) install -r requirements-train.txt

setup-collect:
	$(PIP) install -r collectors/requirements.txt

# --- PHASE A : run the standalone collectors concurrently (raw lake) --------
# Launches the three zero-ML daemons as background jobs and blocks on them so a
# single Ctrl-C (or a service manager stop) tears the whole set down together.
data-pipeline:
	@echo "starting collectors -> $(RAW_LAKE) (Ctrl-C to stop all)"
	$(PYTHON) -m collectors.market_coin.daemon & \
	$(PYTHON) -m collectors.macro_global.scheduler & \
	$(PYTHON) -m collectors.tradfi_assets.poller & \
	wait

# --- PHASE A.5 : the multi-frequency glue (raw lake -> feature store) --------
data-glue:
	$(PYTHON) -m training.quant.feature_pipeline \
		--raw-root $(RAW_LAKE) \
		--out-dir $(FEATURE_DIR) \
		--symbols "$(SYMBOLS)"

# --- PHASE B : multi-symbol-safe labeling on the CLEAN feature store --------
data-label:
	$(PYTHON) -m training.quant.label_symbol \
		--input $(FEATURE_DIR) \
		--output $(PROCESSED)/quant_labeled.parquet \
		--holdout-ratio $(HOLDOUT)

# --- PHASE B.5 : offline verification of the labeled outputs -----------------
backtest-baseline:
	$(PYTHON) -m training.evaluation.backtest \
		--labeled $(PROCESSED)/quant_labeled.parquet \
		--baseline $(BASELINE)

# --- PHASE C/D : run the training smelters -----------------------------------
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

# --- PHASE E : ship the finished goods ---------------------------------------
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
	@echo "removing generated feature store in $(FEATURE_DIR)"
	rm -f $(FEATURE_DIR)/*_features.parquet $(FEATURE_DIR)/features_*.parquet
