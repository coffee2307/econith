# ECONITH Phase 1 — simple, auditable operator test lab

**Read this first:** Phase 1 is **not complete** just because a real public
Binance smoke test passes. The as-of BTCUSDT original/receiver/checkpoint
research path is optional and isolated from real exchange orders, but the
default `main.py` path still uses the legacy unverified market publisher.
The original institutional exit requires both 24–72h real host/network
acceptance **and** audited default runtime migration plus wider
multi-symbol/OHLCV and applicable derivative/macro source coverage. No
automated test here alters the default, enables REALITY, merges PR #28,
or sends any exchange order.

## One-command Windows workflow

Use an existing reviewed checkout of the **Draft PR #28 feature branch**,
not an outdated `main` working copy. The directory must actually
contain `scripts/phase1-test.ps1` and `ci/phase1_operator_lab.py`.
No script silently runs `git checkout`, overwrites your work, deploys
code, or changes the project's execution policy.

Open PowerShell **in the ECONITH repo** (e.g. `F:\econith`).

**Step 1: see the machine and remaining disk capacity (NO network):**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\phase1-test.ps1 -Mode Doctor
```

The report prints CPU count, physical/available RAM, free disk at the
selected evidence destination, Python version and whether pinned
dependencies are installed. It warns if RAM available is less than
1.5 GiB or disk has less than 4 GiB. It never uploads host diagnostics.
No GPU or Docker is needed for this phase.

**Step 2: install the reviewed Python dependencies ONCE if missing:**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\phase1-test.ps1 -Mode Doctor -Setup
```

This creates an isolated local `.venv` and installs the repository's
**pinned** `requirements.txt`. It does not install ML training extras,
start servers, configure a secret, or use containers. Close heavy
applications beforehand if the available RAM is low. A Python 3.12
runtime is recommended (the same major.minor used in GitHub CI).
The setup action explicitly installs packages from PyPI; review
`requirements.txt` before running it if needed.

**Step 3: run one short *public market-data only* smoke trial:**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\phase1-test.ps1 -Mode Quick
```

The `Quick` selection is the user's explicit opt-in to public Binance
market-data network access. Internally it runs the reviewed CLI with
`--allow-public-market-data --single-socket --persistent-receiver`
and three independently persisted segments, up to 160 messages and
45 seconds per segment, with a 100 MB evidence ceiling. A real source
could fail or end before three sessions are accepted; this is evidence
of an actual limitation rather than an invitation to fabricate PASS.

Outputs are placed **outside the Git checkout** by default, alongside
it under `ECONITH-Phase1-Evidence\UTC_DATE_quick_RANDOM\`, with
`evidence/` (original/outbox/receiver/derived/checkpoint/supervisor
SQLite), `doctor.json`, `launch.json`, `stdout.log`, `stderr.log`,
`REPORT.md` and `report.json`. It never replaces a previous trial.
The console shows how many attempts are STARTED/PASSED/FAILED. If
interrupted, incomplete evidence is preserved for forensic review.

**Step 4: inspect/recreate a report from a saved trial:**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\phase1-test.ps1 `
  -Mode Report -TrialDir "F:\ECONITH-Phase1-Evidence\<ACTUAL_TRIAL_DIRECTORY>"
```

Replace the placeholder with the **actual path printed by the script**.
The report refuses inconsistent SQLite or falsely claimed verified
history gaps, and explicitly distinguishes a bounded segment PASS
from 100% institutional exit.

Optionally set `-OutputBase "D:\ECONITH-Evidence"` on Doctor and
Quick to use a drive with adequate free SSD space. It must be outside
the repository and accessible to the operator.

## Linux / CPU-only VPS / optional RunPod CPU instance

Ubuntu 24.04 with Python 3.12, git, a normal unprivileged login, two
vCPUs, ~4–8 GiB RAM and at least 20–40 GiB SSD is a practical *initial
sizing estimate* for this **single BTCUSDT public market research
trial** (not a benchmark or throughput guarantee). 4 vCPU/8 GiB RAM
provides margin for sustained 24–72h monitoring and diagnostics.
Stable internet and durable disk matter more than GPU FLOPS.  No
NVIDIA GPU, CUDA, Redis, Docker daemon or exchange account is needed.

From a reviewed clone of the same Draft PR branch, run:

```bash
bash scripts/phase1-test.sh setup
bash scripts/phase1-test.sh doctor
bash scripts/phase1-test.sh quick
```

Only after a **successful and reviewed Quick trial** consider:

```bash
bash scripts/phase1-test.sh hour
# An optional operator-controlled duration research trial:
bash scripts/phase1-test.sh day
```

An Hour/Day preset uses the same one-socket read-only research source,
persistent HMAC receiver and per-segment SQLite attestation. Hour
has a 1-hour target, Day a 24-hour target, with bounded 2,048
attempts and 1.8 GB file cap. The current finite-segment engine may
exhaust attempts/disk before the target: the CLI will return nonzero
and `REPORT.md` will say `DURATION_TARGET_NOT_REACHED`; **do not
reinterpret that as completed endurance acceptance**. An operator
should keep the shell/session connected or use a manually configured
persistent terminal; no unattended auto-restart or crash evidence
overwrite is implemented.

**RunPod:** its CLI supports creating CPU-only Pods, but public CPU
availability/pricing needs checking at provision time. Paying for
H100/A100/RTX just to capture and validate Binance WSS+SQLite is
unnecessary. A conventional CPU VPS is generally a better default
for network/availability tests. Verify that Binance public endpoints
are accessible and permitted in the chosen cloud region; never
silently bypass a regional access restriction.

## What to send for independent review

Prefer sharing `report.json`, `REPORT.md` and sanitized
`stdout.log`/`stderr.log`. If an original/ACK mismatch must be
investigated, preserve the whole trial folder and contact the
reviewer before deleting files. Raw market records are public data,
but logs/machine information may still contain system metadata.
Do NOT share account credentials or private tokens; none are
needed for this test.

**Result classification:**
- **BOUNDED_SEGMENTS_ACCEPTED_WITH_UNKNOWN_HISTORY_GAPS**: finite
  research sessions accepted, NOT complete market-history data.
- **FAILED_OR_QUARANTINED_SOURCE**: at least one source failed; even
  if a later snapshot restored present-time book, the trial needs
  incident review.
- **UNRESOLVED_INTERRUPTED_SOURCE**: process stopped without a
  verified source close; do not silently restart over the same root.
- **DURATION_TARGET_NOT_REACHED**: the attempt/file budget ended
  before the operator time target.
- **NO_ACCEPTED_SESSIONS**: no accepted observation.

No operator report changes the frozen Phase 1 institutional
exit-gate denominator or claims actual profit/trading readiness.
