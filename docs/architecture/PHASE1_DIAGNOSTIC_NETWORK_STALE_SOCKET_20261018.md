# ECONITH Phase 1 — Real post-optimization operator diagnostic incident

## Operator evidence; no fabricated acceptance

The **operator-provided** immutable local trial:
`F:\\ECONITH-Phase1-Evidence\\20261018T144243Z_diagnostic_fc394ae7`,
run at Git commit `79de2145888a9a766a7d539960ee0b38e42dd2d4`,
found 3 admitted / 1 PASS / 2 FAIL in three bounded 500-frame
sessions. Failure 2: `ConnectTimeout`, original queue peak 0.
Failure 3: `SPOT_CAPTURE_SOURCE_EVENT_STALE`, queue peak 17.
Maximum overall queue peak **58 of 128** (previous diagnostic
saturated at 128), host RAM available min **2.86 GiB** across 17
samples, independent original/receiver proof only on accepted
session (501 originals / 493 ACKs). One outage remains open.
No gap-free or continuous market history. No exchange orders.

The queue improvement is one observational comparison, NOT proof
of long-run reliability or latency percentile. The two new failures
mean the Diagnostic test still FAILED.

## Causal risk found by reviewing the shared public TLS WSS lease

The `single_socket_watch` previously preserved a socket from a
PASSED segment while the next attempt performed its **public clock
preflight** (an HTTPS `/api/v3/time` request). If that request
raised `httpx.ConnectTimeout`, the supervisor recorded the failure
**before** calling the capture factory. Thus the cleanup in the
factory was never called. The same socket remained open while the
system waited for subsequent attempts, but no task was reading it.
For a Binance stream this allows the library and network to buffer
old WSS messages. The next attempted segment could inherit frames
with exchange timestamp too old at the next *local receipt*, triggering
the correct fail-closed `SOURCE_EVENT_STALE` guard (10-second
max_age_ms). This is a technically plausible connection between the
observed iteration-2 `ConnectTimeout` and iteration-3 stale source;
the original aggregate report cannot prove which exact stale frame
was buffered.

## Narrow corrective measures

- The `run_single_socket_forward_watch` wrapper now owns preflight
  cleanup as well as capture-failure cleanup. Any clock-check exception
  or false return first invalidates/closes an inherited WSS lease,
  forbidding reuse after a failed clock preflight.
- A known `httpx.ConnectTimeout` at the public clock preflight is
  reclassified into the approved STATIC sanitized reason
  `SPOT_TRANSPORT_CLOCK_PREFLIGHT_CONNECT_TIMEOUT`, avoiding any
  leaked URLs, request headers or raw network exception text.
  This is a **FAIL**, never an ignored timeout or auto-passed session.
- Added explicit opt-in flag `--fresh-socket-per-segment`, allowed
  ONLY with `--single-socket` and `--persistent-receiver`. The
  Phase 1 `Diagnostic` profile now supplies it automatically.
  In this profile, a successful bounded WSS lease is disposed after
  sealing that segment. The next segment establishes a fresh TLS
  WSS and REST snapshot after clock preflight, isolating contamination
  from stale unobserved socket backlog. The default Quick/Hour/Day
  configurations are unchanged; further optimization of long watch
  lease freshness is tracked separately.
- No change to 10-second max-age, exchange timestamp, REST book bridge,
  source time ordering, HMAC receiver ACK, full checkpoint audit,
  bounded FIFO, or the safety policy about UNKNOWN market history
  between sessions.
- Offline tests use real SQLite/HMAC receipts plus injected pinned
  public lease fixtures, testing fresh lease per independent segment,
  timeout after successful capture, third-session recovery on fresh
  socket with unread deliberately stale frames remaining in old
  socket, and invalid CLI mode combinations. No exchange account,
  keys or public network are used by CI.

## Manual next measurement

Once this exact PR head has green CI, the operator can fetch/merge
it on the feature branch and run one new bounded Diagnostic with >=1.5
GiB free RAM. The new trial should show either distinct failure codes
or accepted independently fresh sockets, but even 3/3 PASS would not
claim continuous full market history or Phase 1 certification.
A network preflight `ConnectTimeout` still legitimately FAILs:
stable connectivity to GitHub over TCP443 by itself does not
prove reliable Binance HTTPS time preflight, nor REST/WSS reachability
during the actual experiment.

If stale events persist on independently new WSS, retain the failed
source and measure exchange-vs-receipt age distribution and local
clock; **never** relax max_age_ms to force a pass. If some trial
fails due to slow public clock preflight, investigate network
stability without automatically substituting an unaudited time source.

No default production/legacy market feed change, real orders or
market strategy execution. Phase 1 estimate remains 80/100, exit OPEN.
