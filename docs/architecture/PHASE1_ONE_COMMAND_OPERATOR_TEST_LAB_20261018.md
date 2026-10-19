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

## Intermediate load test (after a verified Diagnostic PASS, before Hour)

The operator's **public Binance REST clock preflight 5/5 PASS** on
2026-10-18 measured RTT **579–797ms** and exchange-local clock
offset **267–384ms** (thresholds 2000ms and 3000ms). These five
successful calls over a short period do NOT disprove the earlier
1-hour attempt's seven clock `ConnectTimeout` failures. Likewise,
the operator's 3×500-frame Diagnostic PASS did NOT prevent a
128/128 FIFO overflow in a later 1000-frame Hour segment.

To isolate this exact **load/throughput** dimension without consuming
a full hour's attempts, the reviewed `Load` profile runs **3×1000
WebSocket frames**, each with a fresh verified public WSS, fresh REST
snapshot, a single child HMAC receiver and full sealed source/outbox/
derived/checkpoint evidence. Each attempt has a 120s timeout; overall
evidence is capped at 200 MB. It requires >=1.5 GiB available RAM.
It creates a new timestamped evidence folder outside Git and must
be invoked explicitly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\phase1-test.ps1 -Mode Doctor
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\phase1-test.ps1 -Mode Load
```

Do NOT rerun repeatedly on transport failure, and do NOT change
the FIFO limit 128 or 10s source freshness to force PASS. Review the
result's queue high watermark, failed attempt diagnostics, RAM,
outages, unknown segment boundaries and ACK counts. Even 3/3 Load
PASS is three bounded sessions, NOT 1-hour continuous capture,
no proof of gap-free Binance venue history, and no trading approval.
The previously failed Hour evidence remains immutable.

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

An Hour/Day preset uses read-only public Binance Spot WSS
and a persistent HMAC receiver **inside each bounded capture segment**.
As of the real Diagnostic 3/3 acceptance on 18 Oct 2026, Hour/Day
now adopt the Diagnostic **fresh TLS WebSocket lease per segment** policy:
after the previous source is independently sealed, a following
segment opens a new WSS, performs public clock preflight and captures
a fresh REST book snapshot. This avoids inheriting stale queued frames
from an unattended prior socket. No socket/data is reused across
sealed segment boundaries. All such boundaries remain **UNKNOWN history
gaps**; one hour of accepted bounded segments must NEVER be described
as gap-free/continuous 1-hour capture.

Hour has a 1-hour observation *target*, Day a 24-hour target,
with at most 2,048 attempts and a 1.8 GB evidence cap. The bounded
engine may hit file/attempt/time errors before the target; report
and preserve the actual result, never interpret a budget exhaustion
as successful endurance acceptance. The host must have >=1.5 GiB
available RAM on Doctor for Hour/Day admission, working public WSS/
HTTPS, and a supervised PowerShell or terminal session. Do not
unattendedly restart, remove old failed evidence, bypass freshness,
change legacy runtime defaults, or enable live execution.

On Windows, first review a clean `-Mode Diagnostic` and its
`report.json` (including queue, ACK and host-memory evidence),
then explicitly opt in with `-Mode Hour`. Keep the terminal open
and inspect `completion_code`, `failed`, `open_outages`,
`unknown_session_boundaries`, and `gap_free_history_verified`
before describing what was verified.

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
