# ECONITH — Master Roadmap & Next-Conversation Handoff
**Ngày tổng hợp:** 2026-10-18  
**Dự án:** coffee2307/econith  
**Working PR:** [Draft PR #28](https://github.com/coffee2307/econith/pull/28)  
**Nhánh:** `chore/phase0-inventory-hygiene-20261017`  
**Cơ sở kiểm tra trước khi tạo file này:** `332e74f8e17fd26a69e822542454a6e6b506db29` (CI 662 passed, 1 skipped; Docker images/container smoke success).  
**Baseline trước Phase 0/1:** `6ed8775a40cdbc6b8cc5bc7fd008588eabb18bd8`.  

> **Bản kế hoạch thống nhất để sử dụng trong những cuộc trò chuyện tiếp theo.** Khung gốc gồm Phase 0–10; Phase 0.5 được bổ sung sau khi rà soát độ an toàn dữ liệu/khôi phục. Chi tiết gate/mốc nghiệm thu được hệ thống hóa trong file này để tiện thực thi; không phải bản sao nguyên văn của từng cuộc hội thoại trước đó. Cập nhật trạng thái bằng GitHub và CI trước mỗi lần triển khai.

## I. Sứ mệnh / nguyên tắc bất biến

ECONITH ưu tiên trở thành **nền tảng nghiên cứu định lượng, dự báo AI và sau đó mới cân nhắc bot giao dịch crypto đa tài sản**, không dùng kết quả KHKT hay mô phỏng vĩ mô cũ làm bằng chứng trực tiếp cho lợi nhuận trading.

- Ưu tiên kiểm định khoa học có thể tái lập, source/feature/label lineage, as-of availability, loại look-ahead bias, survival/data snooping bias, purging/embargo cho validation.
- Đánh giá **net returns sau fees, spread, slippage, funding, latency, capacity**, drawdown, turnover, tail risk và out-of-sample/walk-forward; so sánh buy-and-hold, random/no-trade, baseline đơn giản.
- Dữ liệu công khai trong SIMULATION **không** cấp phép giao dịch tiền thật; mock fills, trạng thái World hay các kết quả offline không phải bằng chứng từ sàn.
- **Không merge main, không deploy production, không đưa credentials hoặc vốn thật, không khởi động REALITY/execution/live orders** khi chưa vượt qua toàn bộ review gate tương ứng.
- Kế hoạch ưu tiên Python/SQLite/PostgreSQL nghiên cứu, tests, CI, quan sát nguồn và đúng semantics; không coi nhiều dòng code hoặc tỉ lệ kiểm thử pass là lợi nhuận.

## II. Lộ trình 11 Phase + bổ sung Phase 0.5

### Phase 0 — Audit, repository hygiene, baseline an toàn
**Mục tiêu:** biết chính xác ECONITH có gì, điểm nào đang hoạt động, điểm nào chỉ là stub/mock, rủi ro build/runtime/dependency.  
**Các việc:** audit domain Quant/World/AI/Data/API/dashboard; kiểm tra vendor gitlinks, node_modules vô tình commit; làm sạch cây Git có thể kiểm tra ngược; cố định reproducible dependencies, sửa workflows CI; phân biệt SIMULATION/REALITY và quyền runtime; rà migration/config/secrets; xây baseline tests + code ownership inventory.  
**Acceptance:** repo không bị mất source ngoài phạm vi cho phép, workflow YAML hợp lệ và thực sự chạy job, full tests/build/smoke xanh tại HEAD đã review; tránh xóa secrets/volume không được phép.  
**Thực trạng 2026-10-18:** hygiene, CI và docker smoke đã có bằng chứng; PR vẫn Draft, chưa merge.  
**Bằng chứng:** [Phase 0 audit](../audits/PHASE0_REPOSITORY_HYGIENE_20261017.md), [domain map](REPOSITORY_DOMAIN_MAP.md).

### Phase 0.5 — Durability, recovery, order-intent truthfulness
**Mục tiêu:** fail closed từ tầng dữ liệu đến intent/order; không biến restart/timeout thành duplicate order.  
**Các việc:** quyền sở hữu PostgreSQL production/staging; persistent SQLite WAL backups; test SIGKILL/graceful restart/pg_dump-restore trong container disposable; stable intent_id + route plan bất biến; atomic outbox/ACK và replay identity; tách mock TWAP khỏi broker thật; audit endpoint mutation auth.  
**Acceptance:** committed state sống qua forced crash, không giả nhận broker ACK; multi-consumer/retry không tạo ý định trùng; nếu exchange result UNKNOWN phải reconcilie theo client ID gốc trước khi retry.  
**Thực trạng:** nhiều safety component và CI đã có; **broker-side reconciliation/live-execution idempotency chưa được chứng minh**. Không coi Phase 0.5 là chứng chỉ cho real trades.  
**Bằng chứng:** [Recovery/replay gap audit](../audits/PHASE05_RECOVERY_AND_REPLAY_GAPS_20261017.md).

### Phase 1 — Market Data Integrity & Verified Ingestion
**Mục tiêu:** dữ liệu crypto chuẩn nguồn, đúng thứ tự thời gian và mức sẵn có, order book có thể replay/đối chiếu, được xác thực và chuyển bền vững đến consumer nghiên cứu.  
**Các việc:** TLS Binance Spot public aggTrade + depthUpdate + REST snapshot, raw bytes/receipt clock journal, SHA256, as-of, aggTrade continuity, U/u snapshot bridge, reconnect/outages/backpressure, worker lease/fencing, immutable market lineage, producer/receiver SQLite ACK/HMAC IPC, projection/replay/recovery, EventBus admission guard, derived receipt cho observer chỉ báo, verified-only historical ASGI, finite verified public microbatches, test crash/restart và oracle safety.  
**Acceptance phân tầng:**
1. **Đã kiểm chứng:** bounded real public capture, synced replay, injected disconnect, honest history-gap flag, source→independent receiver ACK, historical verified-only research runtime, derived observer durable ledger; CI full suite.  
2. **Chưa kiểm chứng/hoàn thành:** raw-to-consumer **continuous** verified live ingress độ trễ thấp; ACK durable và recovery cho **tất cả** independent consumers; long soak/saturation/network faults; hoàn toàn thay thế/bịt legacy default streamer partial depth20; E2E failover không mất sự kiện được *quan sát*, và xác định rõ khoảng thời gian không thể phục hồi.  
**Trạng thái:** research/offline + finite public **accepted**, Phase 1 **chưa formal 100%** trong phạm vi continuous original. Các đánh giá 99% trước đây chỉ là ước lượng kỹ thuật, **không phải** code coverage/hiệu năng/lợi nhuận.  
**Bằng chứng:** [Exit decision](PHASE1_EXIT_DECISION_20261018.md), [verified research runtime](PHASE1_VERIFIED_RESEARCH_RUNTIME.md), [durable research receipts](PHASE1_DURABLE_RESEARCH_DERIVED_RECEIPTS.md), [real public microbatch](PHASE1_PUBLIC_VERIFIED_MICROBATCH_RESEARCH.md), [crash restart](PHASE1_DERIVED_OBSERVER_CRASH_RECOVERY.md), [receiver batching](PHASE1_BOUNDED_AUTHENTICATED_RECEIVER_BATCH.md).

### Phase 2 — Event-driven backtesting & market simulator
**Mục tiêu:** mô phỏng đầy đủ trình tự signal→risk→intent→order→fill→accounting như một venue event-driven, **không dựa vào vectorized look-ahead**.  
**Nội dung:** event-driven scheduler theo exchange/event/receive/decision/submit/ack/fill timestamps; L2 replay và funding/fees/spread/slippage; partial fill/reject/cancel/latency/queue position; deterministic seeds, ledger đa tài sản, bankruptcy/liquidation.  
**Acceptance:** trên cùng original tape/config/seed kết quả tái lập; không giao dịch trên dữ liệu chưa nhận; parity giữa accounting, positions, equity, fees; stress độ trễ và chi phí. Không dùng random/mock fill làm chứng minh lợi nhuận live.  
**Trạng thái:** **chưa formal nghiệm thu** dưới roadmap mới; repo có các evaluator/backtester lịch sử nhưng không đồng nghĩa đạt phase.

### Phase 3 — Alpha research & empirical validation
**Mục tiêu:** tìm alpha có lợi nhuận sau chi phí, tránh data-mining false positives.  
**Nội dung:** baselines, preregistered hypotheses, candidate features/strategies, factor/risk decomposition; parameter sweep có quản trị; walk-forward, rolling/expanding, purged/CPCV folds với embargo, out-of-sample untouched; bootstrap/confidence bands; market-regime và multi-asset evaluation.  
**Acceptance:** hiệu suất **net-of-costs** dương/ổn định chỉ khi vượt benchmark và kiểm định; báo cáo Sharpe/Sortino/Calmar, drawdown, turnover, leverage, tần suất, coverage, capacity, performance dispersion; tiêu chí reject chiến lược yếu được ghi trước.  
**Trạng thái:** **chưa formal nghiệm thu**. Phase 1 đã chặn một số leakage ở labeler/evaluator, nhưng chưa chứng minh toàn bộ research protocol.  
**Bằng chứng liên quan:** [Temporal leakage audit](../audits/QUANT_TEMPORAL_LEAKAGE_20261017.md).

### Phase 4 — AI forecasting, calibration & regime detection
**Mục tiêu:** dự báo đa horizon/đa asset dựa trên dữ liệu có lineage và đánh giá uncertainty/calibration; chỉ chuyển thành tín hiệu khi giá trị kinh tế được chứng minh.  
**Nội dung:** train registry/model version, dataset snapshots, source/as-of feature gates; baselines statistical/ML, forecast returns/vol/liquidity/regime, ensemble, concept drift; feature importance, calibration, OOS, retraining safety/rollbacks, H200/cloud optional.  
**Acceptance:** leakage-free CV và untouched test, feature availability được xác minh, scoring vs naive baselines, calibration đúng, latency/memory budget, decision utility net costs; rollback trên drift.  
**Trạng thái:** **chưa formal nghiệm thu** cho pipeline định lượng mới; cấu phần AI cũ ≠ trained profitable model.

### Phase 5 — Portfolio construction & risk engine
**Mục tiêu:** biến alpha thành rổ vị thế có risk budget và giới hạn drawdown/exposure.  
**Nội dung:** multi-asset optimizer, transaction-cost/risk-aware allocation, leverage and concentration caps, volatility targeting, correlation regimes, funding and liquidity constraints, circuit breakers, kill-switch, counterparty/venue risk, stress/VAR/ES.  
**Acceptance:** invariant exposure, loss and concentration limits; stress/sim tests chứng minh không vượt các chốt trong dữ liệu trễ/thiếu; cash/margin/PnL accounting đúng.  
**Trạng thái:** chưa nghiệm thu; Sentinel/quant risk legacy chỉ là tiền đề.

### Phase 6 — Execution architecture & broker reconciliation (SIM first)
**Mục tiêu:** một order intent không được bị double-submit; execution states có chứng cứ từ venue.  
**Nội dung:** exchange adapter tách read/trade credential, immutable intent_id/client_order_id, atomic outbox, reconcile order/fill/position/balance, maker/taker fees, min-notional/tick-size, cancel/replace, timeout UNKNOWN, circuit-break, portfolio→execution gating.  
**Acceptance:** chaos test lost ACK/partial fills/restart/partition/retry; NO automatic repeat when uncertain; không bao giờ dùng mock fills như broker proof; mới được phép test sandbox/paper sau duyệt.  
**Trạng thái:** production/REALITY trading **khóa**; Phase 0.5 đã đặt một phần nền tảng, chưa đủ sàn thật.

### Phase 7 — Paper trading, accounting & operational soak
**Mục tiêu:** chạy hệ thống kín vòng signal→execution giả lập/broker paper→accounting trước khi có vốn thật.  
**Nội dung:** paper orders tại môi trường cho phép, ledger reconciliation, fee/funding accrual, source/strategy PnL attribution, multi-week data coverage/soak, incident report, alerts, runbook, RTO/RPO, reconcile after restart.  
**Acceptance:** paper outcomes được đối chiếu độc lập; latency/slippage không thấp hơn thực tế do giả định; no unresolved order UNKNOWN, no silent stale market data, all risk gates green.  
**Trạng thái:** chưa bắt đầu nghiệm thu thật.

### Phase 8 — Autonomous research & experimentation
**Mục tiêu:** AI/agents hỗ trợ nghiên cứu tự động nhưng **không tự cấp quyền chuyển sang tiền thật**.  
**Nội dung:** hypothesis generation, bounded experiment budget, reproducible trial artifacts, model and strategy governance, scheduler, human review of promotion, experiment registry, rollback. World/macro có thể là nguồn giả thuyết bổ trợ nhưng không thay thế market outcomes.  
**Acceptance:** no self-promotion, sandbox isolation, reproducible results and immutable artifacts; con người duyệt mọi risk/production escalation.  
**Trạng thái:** chưa nghiệm thu; autonomous flags legacy không được coi là implemented trading agent.

### Phase 9 — Dashboard, security & reliability
**Mục tiêu:** vận hành, giám sát và audit thay vì chỉ hiển thị demo đẹp. Đây là track **xuyên suốt**, được harden thêm khi tiến dần về production.  
**Nội dung:** data freshness/coverage, lineage dashboard, gate status, latency/backlog, decision→order traces, per-component receipt, health/alerting, secrets/RBAC, API auth, supply-chain scan, backups, DR, rate-limit, container deployment, observability and incident runbook.  
**Acceptance:** không có unauthorized mutation; trạng thái UI bám sát backend; thử downtime/restart/restore, CI security and E2E, measurable SLO/SLA; không public port/keys không bảo vệ.  
**Trạng thái:** có dashboard và CI nền, **chưa hoàn thành vận hành production-grade**.

### Phase 10 — Independent validation, go/no-go and deployment gate
**Mục tiêu:** chỉ đưa tiền thật vào sau external review, OOS economics, technical/safety operations chứng minh được và chủ sở hữu phê duyệt rõ ràng.  
**Nội dung:** independent replication, final untouched OOS, walk-forward/live paper comparability, signed artifacts, fees/liquidity/capacity checks, formal model/risk approvals, rollout canary, rollback, sandbox/capital limits, continuous compliance reviews.  
**Acceptance:** các gate G0–G5 dưới đây đều đạt; không có unresolved P0/P1 blocker; ít nhất một formal human decision ghi vào audit; lỗ hổng thực thi/order/keys bị khóa ngay. **Không đảm bảo lợi nhuận** kể cả khi vượt qua.  
**Trạng thái:** chưa sẵn sàng; **không triển khai production hoặc vốn thật trong PR #28**.

## III. G0–G5 — chuẩn nhóm tiêu chí chốt qua các phase

> Đây là cách **chuẩn hóa đề xuất trong bản master roadmap hiện tại**, không khẳng định từng tên gate giống nguyên văn toàn bộ thảo luận cũ.

| Gate | Trước khi cho phép điều gì | Điều kiện tối thiểu |
|---|---|---|
| G0 — Code/data safety | Tiếp tục xây research foundation | repo/CI/permissions/DB authority, backups, fail closed, modes |
| G1 — Verified market feed | Coi market research dữ liệu có provenance | original bytes/clock/order-book sync/reconnect, durable source receiver receipts, no unmarked coverage gaps |
| G2 — Scientific OOS | Công nhận alpha/model candidate | no leakage, deterministic event backtest, leakage-safe split, strong economic baselines, net fees |
| G3 — Portfolio/risk and orders | Cho phép triển khai sandbox execution | idempotent intent/client order IDs, risk invariants, UNKNOWN reconciliation, crash chaos tests |
| G4 — Paper operational soak | Cân nhắc canary/limited capital | multi-week paper telemetry, broker/accounting reconciliation, alerts, downtime recovery |
| G5 — Independent go/no-go | Bất kỳ đề xuất production/REALITY | external/independent review, security/compliance, signed human approval, reversible rollout |

## IV. Milestones A–F (nhóm lộ trình để báo cáo)

- **A — Foundation:** Phase 0 + 0.5, repository/DB/recovery/auth baselines.
- **B — Trusted data:** Phase 1, original public market data → verified and attributed research outputs; currently advanced but continuous-live blocker remains.
- **C — Evidence of economic edge:** Phase 2–4, event backtesting, validated alpha and forecasting.
- **D — Risk/execution design:** Phase 5–6, risk-aware portfolio and safe broker/order-state architecture.
- **E — Real-world simulation/paper:** Phase 7, reconciled multi-week paper/chaos/latency soak.
- **F — Operations, autonomy governance, deployment gate:** Phase 8–10 with Phase 9 cross-cutting; independent sign-off.

## V. Snapshot tiến độ và số liệu Git (đúng thời điểm trên HEAD 332e74f8)

- PR #28 **Draft**, stacked on PR #21; **main untouched**.
- Baseline `6ed8775a` → `332e74f8`: **251 commits**, **2.730 changed paths** (bao gồm **2.594** tracked root node_modules paths đã dọn và **8** vendor gitlinks không có code thực). Sau khi loại dependencies + 8 gitlinks, còn **128** paths thuộc source/tests/docs/config/build. Khác với chỉ đếm Python logic, số này vẫn bao gồm tài liệu và workflow.
- GitHub PR aggregate: **20.230 lines added**, **89.785 deleted**; số xóa chi phối bởi cleanup dependencies, do đó **không** dùng tổng deletions để báo 89k dòng code lõi được viết lại.
- Exact HEAD GitHub Actions: **662 passed, 1 skipped**, Python CI + Docker images + container smoke success. Đó là test pass count, **không phải độ chính xác dự báo hoặc % lợi nhuận**.
- Verified research cho phép lưu real public bounded sessions, replay/book synchronized, reconnection gap honesty, raw→market lineage, HMAC authenticated receiver IPC, batch receiver process optimization và durable derived observer receipts, including hard-process crash experiments.
- Còn **chưa đạt** original full Phase 1: fully continuous verified-only no-legacy-bypass live data plane, independently durable ACK for arbitrary consumer, long soak and whole-runtime cutover.

## VI. Hướng dẫn chuyển sang cuộc trò chuyện mới

**Tài liệu chính:** file này. Ưu tiên mở GitHub nội dung trên chính Draft branch, kiểm tra PR HEAD và CI mới nhất. Các audit riêng có thể cũ tại thời điểm tạo; đọc ngày và context trước khi kết luận.

**Gợi ý brief copy sang ChatGPT phiên mới:**

> Tiếp tục dự án ECONITH của tôi tại GitHub coffee2307/econith, Draft PR #28, branch chore/phase0-inventory-hygiene-20261017. Hãy đọc docs/architecture/ECONITH_MASTER_ROADMAP_AND_HANDOFF_20261018.md cùng docs/architecture/PHASE1_EXIT_DECISION_20261018.md và PR #28 trước tiên. ECONITH ưu tiên nghiên cứu định lượng/bot giao dịch crypto đa tài sản, đánh giá OOS net phí, tránh look-ahead bias. Phase 0 và 0.5 đã có nhiều chốt an toàn, Phase 1 đạt offline/finite read-only public research, durable HMAC receiver và observer ACK; continuous verified ingress vẫn chưa được formal nghiệm thu. Hãy xác nhận exact HEAD/CI/constraints rồi tiếp tục hạng mục còn mở theo roadmap, tạo tests bằng chứng và update PR. Không merge main, không production, không credentials, không vốn/lệnh thật, giữ PR Draft. Không báo 100% hay lợi nhuận khi thiếu proof.

**Các đường dẫn bổ trợ:**
- Draft PR: https://github.com/coffee2307/econith/pull/28
- Audit Phase 0: [../audits/PHASE0_REPOSITORY_HYGIENE_20261017.md](../audits/PHASE0_REPOSITORY_HYGIENE_20261017.md)
- Audit Phase 0.5: [../audits/PHASE05_RECOVERY_AND_REPLAY_GAPS_20261017.md](../audits/PHASE05_RECOVERY_AND_REPLAY_GAPS_20261017.md)
- Phase 1 exit decision: [PHASE1_EXIT_DECISION_20261018.md](PHASE1_EXIT_DECISION_20261018.md)
- Actual derived crash recovery: [PHASE1_DERIVED_OBSERVER_CRASH_RECOVERY.md](PHASE1_DERIVED_OBSERVER_CRASH_RECOVERY.md)
- Observed market ingestion acceptance: [PHASE1_PUBLIC_VERIFIED_MICROBATCH_RESEARCH.md](PHASE1_PUBLIC_VERIFIED_MICROBATCH_RESEARCH.md)
- Leak audit: [../audits/QUANT_TEMPORAL_LEAKAGE_20261017.md](../audits/QUANT_TEMPORAL_LEAKAGE_20261017.md)

**Update discipline:** Sau mỗi phase hoặc PR thay đổi đáng kể, ghi exact base/head/commit, CI run links, tests pass/fail, number of true source files vs dependency deletions, recorded evidence, unresolved blockers, và trạng thái Draft. Không cho phần trăm chủ quan thay thế acceptance criteria.


## VII. Latest verified Phase 1 handoff — 2026-10-18 (after Load profiling)

This addendum supersedes the older Phase 1 operational snapshot above, **not** the original Phase 0–10 scope or the frozen exit-gate denominator.

- Confirmed same failure class **twice** on independent real Binance public `Load` operator runs: three × 1,000 WS frames, **2 PASSED / 1 FAILED**, source queue saturated **128/128**. The second, instrumented run (new evidence root `20261018T164057Z_load_0eb97efa`) measured per-stage latency *also on the FAILED attempt*. All failed-source originals remain quarantined, not certified.
- Real instrumented attempt queue peaks: **119/128, 128/128, 52/128**. Exactly 2,002 originals and **1,988 receiver ACKs from the two PASSED sources**; failed attempt had 870 original rows and 864 partial forward stages, excluded from acceptance. No exchange orders.
- Offline source-compatible benchmark corroborates the causal mechanism: 240 frames at 3 ms interval fail closed at 128/128, while the same structure at 60 ms reaches 241 originals/239 ACKs and queue peak 2/128. Single capture consumer blocks on a series of durable source/outbox/HMAC/derived/checkpoint work per eligible original.
- A/B experiment parallelizing three independent research derived-ledger commits via threads was **slower end-to-end** and completely reverted. This demonstrates why optimizing one metric (`EventBus.publish`) alone is insufficient.
- New `infrastructure/spot_forward_stage_profile.py` offers bounded aggregate diagnostic timing only on explicit operator `Load` trials; report regeneration shows per-attempt top stages but does NOT alter verdict or source evidence. Offline benchmark entrypoint `ci.phase1_offline_burst_probe` makes the failure reproducible on a lightweight CPU host.
- **Critical next engineering increment** is reviewed **source-first durable spool and restart-recoverable projection separation** with explicit delivery-lag/freshness and disk quota. Safeguard 128-frame ingress, FULL SQLite durability, generation-fenced ownership, independently authenticated receiver, named derived receipt integrity and UNKNOWN history gaps. Do not restart Hour/Day until controlled real Load acceptance is re-proven after a justified design change.
- Full institutional Phase 1 **OPEN**, prior provisional **80/100 engineering estimate unchanged**. In addition to backpressure, remaining P1-04/05/06/07 gates include physical failure acceptance, genuinely continuous verified ingress, default legacy→verified-only cutover, multi-symbol/OHLCV/conditional source coverage, and long real operator soak. Never mark 100% from 2/3 Load or green CI.

**Authoritative incident record:** [PHASE1_BACKPRESSURE_ROOT_CAUSE_20261018.md](PHASE1_BACKPRESSURE_ROOT_CAUSE_20261018.md). **Original gate matrix:** [PHASE1_IMMUTABLE_EXIT_MATRIX_20261018.md](PHASE1_IMMUTABLE_EXIT_MATRIX_20261018.md). Operator evidence stays outside Git at `F:\ECONITH-Phase1-Evidence`; no original captures have been uploaded or rewritten. PR #28 remains DRAFT, no production/REALITY/capital permission.


## VIII. Source-first sealed durable spool milestone — 2026-10-19

New opt-in SIMULATION/verified research architecture **successfully captures one real public Binance Spot BTCUSDT session of 1,000 WS frames without inbound queue overflow**. It uses already-durable original + atomic intent spool, strict incremental source validation, then **after the source is SEALED** reconstructs original-as-of eligible projections and requires independent persistent HMAC receiver ACK, derived durable named observer receipts and chained checkpoints. It does not publish during the OPEN session or imply continuous source coverage.

Operator real public acceptance (new fresh evidence root `F:\ECONITH-Phase1-Evidence\20261019_sealed_public_1000_v1`): **1,001 original, 993 receiver ACK, 993 checkpoint, 1,341 primary derived / 2,682 named receipts; queue peak 42/128, 71.391s capture + 62.422s sealed replay, zero orders, exit 0**. Synthetic 1,000-frame fast burst also PASS at queue 2/128. Local targeted tests 36 PASS; exact code-HEAD GitHub Python CI/Docker build/smoke passed at `ed6ac6b4`. The original in-session Load remains previously 2/3 FAIL and is **not repaired/retested** by this *distinct* delayed sealed delivery profile.

Full evidence/implementation/limitations: [Source-first spool repair](PHASE1_SOURCE_FIRST_SPOOLED_RESEARCH_REPAIR_20261019.md). Open follow-on: **continuous protected source→durable asynchronous projector with source-age/lag bounds, worker restart and crash fault injection, default cutover, 24–72h real soak and multi-symbol coverage**. Frozen institutional **Phase 1 overall OPEN; 80/100 provisional engineering estimate unchanged**. Never claim 100%, production, live orders or guaranteed profitability based on this bounded research PASS.


## IX. Bounded in-session durable spool projector — 2026-10-19

New experimental `spooled_in_session` mode uses a **separately persistent original projection cursor**: ingress commits raw original+intent and validates as-of source; an async worker reads committed original rows, rejects stale source, then commits existing HMAC receiver ACK → derived observer receipts → chained checkpoint → projection cursor, even while source WSS remains OPEN. Disabled by default, fixed 128 ingress, bounded pending records + bytes + 5s source→publication freshness, SIMULATION only. Manual public trial requires explicit double opt-in and clock preflight.

Real public Spot milestones: single 160-frame trial **PASS** (cursor 161, signed ACK 141, input FIFO 16/128, max lag 1,624ms); single 1,000-frame trial **PASS** (cursor 1,001, signed ACK 990, input FIFO 26/128, max lag 3,924ms). However fresh independent public **3×1,000 matrix FAILED: 1/3**: two segments quarantined at `FORWARD_PROJECTOR_SOURCE_LAG`, their original/cursor counts respectively **600/500** and **528/352**, with 100 and 176 committed-but-unprojected suffix records. Third segment PASS 1,001 original/992 ACK, cursor 1,001, queue 43/128, max lag 4,203ms. Failure is a sustained projector service-rate/lag headroom problem, **not** a reason to change the 5,000ms safety limit or certify incomplete source history. Existing original Load, 24–72h soak and default cutover still OPEN.

Dedicated forensic & next steps: [PHASE1_IN_SESSION_SPOOL_MATRIX_20261019.md](PHASE1_IN_SESSION_SPOOL_MATRIX_20261019.md). Source evidence remains on the authorized local PC under `F:\ECONITH-Phase1-Evidence`. No real orders, no capital/REALITY permission. Frozen score **80/100 provisional**, not a new estimate or gate pass. PR #28 remains DRAFT.


## X. Opt-in SQLite observer reuse — 3×1,000 public in-session milestone (2026-10-19)

**Newest progress replaces IX's prior 1/3 outcome for this *new distinct experimental code path***, not the original official institutional acceptance denominator. Root cause refinement: repeated opening/closing of three independent derived SQLite ledger connections consumed high per-event overhead. New opt-in single-session connection reuse, with **unchanged WAL/FULL per-event COMMIT**, reduced publisher overhead in offline A/B; no batching, weakening HMAC, production cutover or changing 5-second freshness cap.

Synthetic same-structure 240-WS-frame / 20ms paired A/B: **baseline 0/3**, connection reuse **3/3**; every PASS had 241 originals/239 verified independent ACKs. First public 160-frame pilot PASS at max source age 626ms. Independent real Binance public clock-checked **3×1,000 WS-frame matrix PASS 3/3**: (original/cursor/ACK/checkpoint/ingress peak/max age) attempts **1: 1001/1001/993/993/23/1425ms**, **2: 1001/1001/991/991/38/3196ms**, **3: 1001/1001/991/991/36/4790ms**. Official operator verdict `PASSED_FINITE_PUBLIC_SPOOL_3X1000_RESEARCH_ONLY`; 251.562s; zero orders. Separate read-only database queries checked all original/cursor/ACK/checkpoint counts. GitHub CI, Docker builds, container smoke green at reviewed code SHA `143d4dc`.

**Critical limitation:** attempt 3 has only 210ms headroom under 5,000ms age limit. The mode remains separately opted in, finite research-only, no certified gap-free uninterrupted market feed, Hour/Day, 24–72h soak, physical hard-kill recovery, default verified-only cutover or expanded asset/OHLCV matrix. Institutional Phase 1 overall stays **OPEN**, frozen weighted engineering baseline **80/100 (not re-scored)**, PR #28 DRAFT, SIMULATION, no orders.

Authoritative evidence and next work: [PHASE1_DURABLE_OBSERVER_REUSE_ACCEPTANCE_20261019.md](PHASE1_DURABLE_OBSERVER_REUSE_ACCEPTANCE_20261019.md). Raw source evidence stays only on the authorized operator host under `F:\ECONITH-Phase1-Evidence`. Next: independent SLA lag histograms, timeout/host kill/disk/fencing tests and sustained timed source admission before any wider rollout.


## XI. Audited admission-age histograms + real hard-exit gate — 2026-10-19

New constant-space `projector_lag.json` per finite in-session spool segment records bucketed admission-age **p50/p95/p99**, exact max/headroom and backlog peak, including *failed* quarantined attempts. Definition: original local receive time → immediately pre-protected-EventBus admission, recorded on successful publish; **NOT** network RTT or consumer delivery completion. Frozen 5,000ms rejection remains unchanged. A finite public segment now requires histogram admission sample count to reconcile 1:1 with HMAC receiver receipts before it is credited as PASS.

**Real public re-test** after telemetry: a 160-frame BTCUSDT source **PASS** (161 originals, 152 independent receiver ACK/checkpoints, cursor 161, p95<=2,049ms, p99<=2,149ms, exact max 2,105ms). A new independent public **3×1,000-frame matrix PASS 3/3**, 245.235s: attempts 1–3 original/cursor **1,001/1,001 each**, receiver ACK/checkpoints **992,992,990** respectively, ingress peaks **39,59,38/128**, admission-age p95 upper **1,349/2,699/3,449ms**, p99 upper **1,849/3,299/4,099ms**, exact max **2,047/3,353/4,197ms**, minimum freshness headroom **2,953/1,647/803ms**. Independently audited the three per-source SQLite ledgers and histograms in read-only mode; no unprojected originals or leftover owner markers. 0 orders, bounded research only.

A direct child-process `os._exit` between durably committed checkpoint and source cursor has also been exercised. `ci.phase1_spool_forensic_inspect` provides **read-only** source/cursor/ACK count checks and reports owner fence existence without guessing process liveness; full cryptographic source/HMAC certification, operator recovery, history continuity and real power-loss resilience remain separate.

Detailed evidence: [PHASE1_LAG_HISTOGRAM_HARDKILL_ACCEPTANCE_20261019.md](PHASE1_LAG_HISTOGRAM_HARDKILL_ACCEPTANCE_20261019.md). Historical pilot and full-source evidence remain outside Git at `F:\ECONITH-Phase1-Evidence`. **Phase 1 remains OPEN**, frozen provisional weighted engineering baseline **80/100** (NOT automatically re-scored), PR #28 DRAFT, no main merge, production or live orders. Next: safe timed soak with explicitly UNKNOWN disconnected gaps and a reviewed orphan-recovery protocol, then separately verified-only cutover and broader source matrix.
