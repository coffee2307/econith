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


## XII. Re-scored Phase 1 and honest failed 10-minute read-only spool watch — 2026-10-19

Frozen institutional seven-gate matrix denominator remains **100**. Evidence-credit re-score **80→81/100 provisional**, solely **P1-05 12→13/15** from two independent real Binance finite 3×1,000 in-session source/HMAC/cursor/derived/checkpoint PASS matrices and p95/p99 age histogram. P1-01 20, P1-02 15, P1-03 15, P1-04 14, P1-05 13, P1-06 4, P1-07 0. **Still OPEN, not live or production certification.** See [PHASE1_WEIGHTED_RESCORE_20261019.md](PHASE1_WEIGHTED_RESCORE_20261019.md).

New explicit `ci.phase1_public_spool_timed_watch` offers a finite, locally resource-monitored 10min/15min maximum experimental public Spot research **multi-session** watch. Fresh source and ledger per session, independent signed receiver, 5s freshness, max 128 input queue, FULL per-event SQLite, 500MB evidence cap, 2GB free disk and 1GB free physical RAM thresholds; explicit unknown inter-session gaps and per-segment durable verdict reports. Unit tests passed before public trial.

**Actual 10-minute target FAILED**: source trials on authorized host at `F:\ECONITH-Phase1-Evidence\20261019_public_spool_timed_watch_10min_v1`: 4/5 bounded 1,000-frame sessions passed, 5th quarantined with `FORWARD_PROJECTOR_SOURCE_LAG`, watch exited 1 after **396.218s**, target 600s **not reached**. Failed session 738 original commits, cursor 646, **92 unprojected originals**, 638 authenticated HMAC receiver rows vs **637 fully chained checkpoints**; failed histogram 637 samples with max accepted age 4,974ms before stale event refusal. Never certify partial-source data. One session observed free RAM down to ~1.10GB after pass, near threshold, but **causation is NOT proved**; do not attribute lag automatically to RAM pressure. Source boundaries 4 UNKNOWN; 0 orders.

For full per-session verified forensic table, see [PHASE1_10MIN_WATCH_FAILURE_20261019.md](PHASE1_10MIN_WATCH_FAILURE_20261019.md). Next must diagnose burst-driven source→projector throughput and host I/O/memory, review orphan recovery/fencing, then safely retry a 10min watch only after tests and resource headroom. No automatic repeated soak, 24–72h claims, default verified-only cutover, main merge, real brokerage routes or capital use. Draft PR #28 unchanged.


## XIII. Source-versus-projector pressure telemetry and producer connection reuse (2026-10-19)

After the previous 10-minute read-only multi-session watch **FAILED** at 396.218s (4 accepted, 1 `FORWARD_PROJECTOR_SOURCE_LAG` quarantined), new `infrastructure/spot_spool_pressure_diagnostics.py` was added as separate optional 5-second aggregate pressure telemetry. It measures raw post-commit callback rate, projected cursor rate, EventBus/checkpoint event counts, pending original peak, admission lag peak, Windows free RAM and process CPU time, with the existing per-stage latency profiler switched on only for an explicit `--diagnose-pressure` trial. No payloads/source IDs are persisted in its diagnostic JSON; it is not source proof. The real operator profile and defaults remain unchanged unless explicitly opted in.

Controlled fixture 240 WS/20ms diagnosed first 5s burst **216 originals vs 104 cursor completions** (~43.2/s ingress vs 20.8/s projection); backlog peaked >100. A new opt-in experiment caches **independent producer outbox and chained checkpoint SQLite connections** while keeping `WAL`, `synchronous=FULL`, independent database ownership, and separate full COMMIT for *every* stage; separate experimental flags and fail-closed crash/permission tests apply. Two sequential identical-structure offline A/B pairs with existing derived cache reduced full-run time **10.22→6.67s** and **10.20→6.76s**; highest admission age roughly halved. Hard-kill tests preserve source-orphan fencing. No implicit runtime or public rollout occurred.

One real public Binance 160-frame pilot with cached producer PASS: 161 originals/cursor, 152 HMAC ACK/checkpoints, max age 162ms, queue 6/128, 0 orders. **One public 1,000-frame pilot also PASS, BUT NEAR LIMIT:** 1,001 originals/cursor, 992 HMAC ACK/checkpoints, queue peak **123/128**, 163 peak unprojected original backlog, age max **4,586/5,000ms**, only **414ms** freshness headroom; a 5s window saw 211 arrivals versus 150 completions. This is **not** grounds for renewing 10min, Hour or Day soak on a resource-constrained shared Windows host. The original failed-soak verdict and 4 unverified inter-session history boundaries remain untouched. No orders sent.

**Formal frozen institutional Phase 1 remains OPEN, provisional engineering re-score 81/100 unchanged** (P1-04 14/15, P1-05 13/15, P1-06 4/10, P1-07 0/10; rest fully credited only within reviewed narrow scope). PR #28 DRAFT, no main merge, default cutover, REALITY or production. Evidence details and isolated original paths in [PHASE1_PROJECTOR_PRESSURE_AND_PRODUCER_REUSE_20261019.md](PHASE1_PROJECTOR_PRESSURE_AND_PRODUCER_REUSE_20261019.md). **Next:** investigate HMAC receiver scheduling/outliers and input FIFO pressure with finer time windows and adequate host headroom, tune only if no durability loss, repeat bounded source trials, then plan new witnessed soak.


## XIV. Opt-in persistent HMAC receiver connection caching + FIFO/HMAC stall correlation — 2026-10-19

After prior cached-producer 1,000 public frames **PASS but nearly exceeded** the 128 ingress queue (123/128) and 5,000ms age gate (4,586ms), the independent **HMAC receiver child** was profiled: per-envelope fresh SQLite connections remained a small but repeatable overhead. New *separate opt-in* cached child receiver connection holds its own WAL/FULL connection across an individual bounded source, keeping the strict signed protocol, independent per-envelope full COMMIT, producer ACK ordering, outbox, derived, checkpoint, orphan fencing and default `SIMULATION`. No implicit public/production enablement or relaxation of age, queue or identity constraints. Caches require separately opted-in bounded spool, verified derived and producer cache path.

Two independent deterministic-structure 240-frame/20ms offline A/B pairs reduced receiver ACK mean **7.32→5.40ms** and **7.31→5.60ms**, ACK p99 **19.02→10.95ms** and **19.47→12.19ms**, run wall time **6.41→6.25s** and **6.58→6.28s**, all original/cursor 241/241, HMAC ACK/checkpoint 239/239. New five-second aggregate diagnostics include input FIFO depth, >=90% full observations, ACK peaks and counts >50/250/500ms while retaining no payloads or secrets.

Manual real Binance 160-frame pilot with full opt-in receiver/producer/derived cached path **PASS**: original/cursor 161, signed ACK/checkpoint 151, queue 9/128, backlog 11, highest admission age 236ms, p99 bucket upper 249ms, no orders. Local evidence: `F:\ECONITH-Phase1-Evidence\20261019_public_receiver_cache_160_v1`. Child genuine HMAC protocol and OS hard-kill tests passed for this experimental path. Separate synthetic 240-frame sample verified pressure histogram, including one HMAC ACK >50ms (172ms max) and ingress FIFO only 2/128 despite downstream original backlog >100.

**Frozen Phase 1 remains OPEN at 81/100 proposed evidence credit.** Do not confuse limited 160-frame PASS or offline performance with proof of high-load Binance 1,000-frame headroom, uninterrupted 10min / Hour / 24–72h operation or gap-free exchange history. The real 10min watch from earlier STILL FAILED at 396.218s. Preserve its forensic evidence and unknown boundaries; no auto reruns, live orders, main merge or default verified-only cutover. Detailed handoff: [PHASE1_HMAC_RECEIVER_CACHE_AND_FIFO_PRESSURE_20261019.md](PHASE1_HMAC_RECEIVER_CACHE_AND_FIFO_PRESSURE_20261019.md). Next: correlate HMAC stalls with >90% input FIFO on comparable live burst, inspect resource headroom, then consider a new bounded larger-load run ONLY after safety criteria are met.


## XV. Captured original SQLite writer cache + independent 1000-frame public check — 2026-10-19

New fully isolated, explicitly opted-in original+capture-intent WAL/FULL single-session SQLite connection reuse cuts repeated connection startup without weakening the per-original atomic COMMIT and SHA source/intent verification. It is disabled by default and publicly eligible only under the existing explicit finite public spool gate with all derived/producer/receiver cache flags, 128 FIFO and 5000ms freshness. Physical subprocess os._exit after a raw+intent COMMIT, trigger-induced transaction rollback and independently reopened source/intent DB verification are covered; the hard-exit checkpoint-to-cursor test now also includes the fully cached original writer. A receiver test race during initial schema creation was isolated and corrected in the test only.

Two sequential identical synthetic 240-frame/20ms A/B pairs with all other caches matched: original source SQLite commit mean 4.189->0.767ms and 4.145->0.781ms, end-to-end 6.59->5.17s and 6.38->5.95s, pending original peak 116->2 and 108->4, all PASS original/cursor 241 and HMAC ACK/checkpoint 239. More extreme 240-frame/5ms fixture still accumulated ~200 pending originals in either variant; no all-conditions capacity proof.

Separate real Binance public finite 160-frame original-cache pilot PASS (original/cursor 161, HMAC ACK/checkpoint 150, FIFO 14/128, max age 184ms, no orders); one **real 1000-frame pilot PASS** (original/cursor 1001/1001, HMAC ACK/checkpoint 992/992, queue **48/128**, original pending peak **78**, max age **1053/5000ms**, p99 age upper **949ms**, zero orders). Read-only independent on-disk SQLite verification confirms aligned ledgers, closed original source, 0 unprocessed suffix and no owner fence. Seconds 5-10 had 181 arrivals vs 107 cursor completions; no HMAC ACK >50ms in that window, so transient ingress/projector imbalance remains. Previous non-original-cache public sample had queue 123/128 and max age 4586ms, **but they experienced different market bursts and cannot be directly treated as controlled percent improvement**.

New evidence file: [PHASE1_ORIGINAL_CAPTURE_CACHE_PUBLIC_20261019.md](PHASE1_ORIGINAL_CAPTURE_CACHE_PUBLIC_20261019.md); all original market evidence remains on Windows under F:\ECONITH-Phase1-Evidence. **Frozen institutional P1 exit remains OPEN at 81/100 provisional**. Historical failed 10-minute watch (396.218s, 4/5) is still FAILED. PR #28 Draft, SIMULATION only, no main merge, default ingress cutover, capital or real orders. Next: audited crash/regression and isolated 3x1000 trial if resource headroom permits, then real timed observation and the remaining P1-04/05/06/07 gates.

## XVI. Fully cached 3×1000 Binance matrix operator is ready, but resource preflight BLOCKED run — 2026-10-19

Commit series after 405b80f adds full-cache explicit operator flags for original, receiver, producer and derived SQLite, plus independent per-source host pre/postflight and fsync+atomic partial-progress reports. It retains default-OFF flags, the 128 FIFO, at most 1000 WSS frames, 5000ms source freshness, WAL/FULL per-event durability, per-source public clock checks, no orders, and UNKNOWN history between sessions. Targeted Windows tests passed 16/16 including resource denial and checkpointed partial reporting.

Read-only operator host probes showed RAM availability dropping from ~1.18GB to ~0.89GB (8GB host) with Chrome/ChatGPT/PHAM/Beatriz workloads; F: free disk >76GB. The new operator's RAM minimum is 1.50GB. The **3×1000 public full-cache matrix was NOT launched** on this insufficient-resource host; no PASS/FAIL against Binance may be claimed for it. Source performance/soak gate P1-05 stays PARTIAL and the frozen proposed overall engineering score remains **81/100**, institutional Phase 1 OPEN. Previous finite full-cache 1000-frame solo PASS and historical FAILED 396.218s 10-minute watch are preserved unchanged.

Full implementation, test policy, blocker and future command: [PHASE1_FULLCACHE_MATRIX_RESOURCE_GATE_20261019.md](PHASE1_FULLCACHE_MATRIX_RESOURCE_GATE_20261019.md). Next when RAM stable above 1.50GB: verify GH CI and independent host resources, run a fresh 3×1000 only with all explicit flags, inspect separate source/cursor/HMAC receiver/checkpoint proof and p95/p99, then decide if a new timed watch is safe. Do not reduce resource thresholds, stop other private app processes, merge Draft PR #28, deploy to production or permit real orders just to obtain a pass.


## XVII. Cloud runner (16GB) independent Binance 3×1,000 finite full-cache PASS — 2026-10-19

Windows 8GB host had <1.5GB free RAM and its matrix was correctly BLOCKED without network calls. We moved the same read-only SIMULATION full-cache research profile to an isolated public-repo GitHub Ubuntu 24.04 runner (4 vCPU, 16GB RAM; preflight 15.68GB free). One-off workflow on `experiment/phase1-cloud-public-20261019` (no main merge) used an immutable commit source archive to avoid a pre-existing malformed `econith_social` submodule metadata issue, and then ran 20/20 unit checks and the **real public Binance finite 3×1,000 matrix in 212.807s: 3/3 PASS**.

Original/cursor counts 1001/1001 in each source; independent HMAC receiver ACK and chained checkpoint counts 991, 987, 976; ingress FIFO peaks 11, 8, 25 out of 128; max ages 395, 250, 581ms (limit 5000ms); p99 bucket upper bounds 349, 149, 549ms; worst headroom 4419ms. Independent cloud on-runner read-only SQLite/intent SHA/count/PRAGMA integrity audit **3/3 PASS**, zero pending original suffix, no owner fences, no orders. Inter-session history gaps remain UNKNOWN and no continuous source is certified. Evidence: [PHASE1_CLOUD_3X1000_FINITE_ACCEPTANCE_20261019.md](PHASE1_CLOUD_3X1000_FINITE_ACCEPTANCE_20261019.md). GitHub Actions run: https://github.com/coffee2307/econith/actions/runs/39252752983 (SUCCESS); aggregate-only, 3-day retention artifact: https://github.com/coffee2307/econith/actions/runs/39252752983/artifacts/12187180009 . Original SQLite bytes were deliberately NOT uploaded to a public repo: the after-run numeric proof cannot replace a durable private institutional original ledger archive.

**Formal Phase 1 remains OPEN, proposed evidence credit 81/100 unchanged.** A finite cloud PASS must not erase the failed Windows 10-minute watch at 396.218s, be interpreted as a 10-minute uninterrupted WSS, or justify enabled production/broker orders. Next staged increment: portable, resource-gated timed cloud watch with fully explicit cache flags, preserve incomplete/source-history-UNKNOWN labels, then proper private raw-evidence retention and subsequent original P1-04/05/06/07 gates.


## XVIII. Cloud bounded 10-minute soak FAILED on sixth ingress queue — 2026-10-19

GitHub Actions run https://github.com/coffee2307/econith/actions/runs/39254489969 at dedicated one-shot cloud watch branch `experiment/phase1-cloud-watch-20261019` passed Linux 16GB host resource preflight and 20 focused crash/recovery tests, then ran six new independent real public Binance Spot sessions. Sources 1–5 each passed 1,000 frames with 1,001 durable originals/cursor, 4,953 total independent HMAC receipts equal chained checkpoints, ages comfortably <5,000ms. Sixth source was **FAILED_OR_QUARANTINED** at 57.719s for `SPOT_TRANSPORT_QUEUE_BACKPRESSURE` with only **909 durable originals/cursor, 900 HMAC ACK/checkpoints**, not the required 1,001 original positions. **Actual watch 427.066s, target minimum 600s NOT MET; overall FAILED (5/6 attempts accepted), 5 inter-session history boundaries UNKNOWN.** The independent read-only SQLite forensic check confirmed 5 finished sources, 1 unfinished source, no source-intent SHA discrepancies in reviewed rows. Zero orders and no production.

Negative result is separate from both previous **cloud finite 3×1,000 PASS** and **Windows aborted 396.218s watch**. Preserve every verdict. The new failure is transport FIFO capacity, not proof of the historical projector lag root cause; examine per-window FIFO queue/ingress, original commit, HMAC and host scheduling from aggregate-only artifact https://github.com/coffee2307/econith/actions/runs/39254489969/artifacts/12186544460 (3-day retention). Source SQLite files were ephemeral and deliberately not uploaded to public GitHub: institutional raw retention gate still missing.

See [PHASE1_CLOUD_10MIN_WATCH_FAILURE_20261019.md](PHASE1_CLOUD_10MIN_WATCH_FAILURE_20261019.md). Frozen Phase 1 **OPEN, 81/100 proposed engineering evidence credit unchanged**. Next: forensic review and offline burst fault injection before any new public soak; retain unchanged queue 128, 5s freshness, WAL/FULL commits, independent HMAC and history UNKNOWN between reconnects. No automated retry, main merge or real orders.


## XIX. Subsecond 250ms ingress queue forensics after failed 427s cloud watch — 2026-10-19

Independently retrieved the original 3-day GitHub artifact from FAILED cloud timed watch https://github.com/coffee2307/econith/actions/runs/39254489969 . Its sixth source aborted on `SPOT_TRANSPORT_QUEUE_BACKPRESSURE`: in 45–50s it committed/advanced 53/53 original records with FIFO peak 1; at 50–55s it committed/advanced **191/191** and yet FIFO reached **128/128**, 14 observations ≥90% full and peak pending originals 167, showing a short-lived queue buildup **not explained by equal five-second end-to-end counts**. Sixth-source source SQLite p99 1.874ms, HMAC receiver ACK p99 3.810ms and EventBus publish max 76.485ms. They cannot be assigned causal timing to FIFO overflow from these broad histograms. The old Linux 5-second per-window RAM field was null.

New optional 250ms bounded diagnostic now records enqueue/dequeue/writer-complete/overflow counts, near-full events, peak depth and writer service peak. Memory capped to 960 windows, no individual payloads, timestamps, user data or source identifiers. Integrated only when the explicit pressure diagnostics flag is used; still `put_nowait` fail-closed FIFO 128, strict SQLite `WAL/FULL`, 5s freshness, HMAC independent ACK and zero trading. An aggregate-only read-only forensic CLI pinpoints a measured overflow and preceding windows. Deterministic no-network burst overflow tests prove refusal to certify the source. Windows regression **59/59 PASS** and 240-frame 5ms full-cache offline trial PASS with source/cursor 241, HMAC ACK/checkpoint 239, enqueue/dequeue/writer-complete each 241 and no overflow.

A single new cloud Binance Public **1,000-frame pilot PASS** with micro-telemetry: https://github.com/coffee2307/econith/actions/runs/39269607804 ; original/cursor 1001/1001, independent HMAC ACK/checkpoint 988/988, FIFO peak **20/128**, max age **373/5000ms**, p99 upper 349ms, zero orders. 228 subsecond windows contain 1001 enqueue / 1001 dequeue / 1001 writer completions / 0 overflow, no truncation; a single 250ms interval had **67 enqueue / 67 dequeue** (local average 268/s). One HMAC IPC max was 211ms but occurred with no FIFO overflow and cannot alone explain prior failure. Runner memory about 15.7GB. Aggregate-only artifact: https://github.com/coffee2307/econith/actions/runs/39269607804/artifacts/12195775164 (3-day retention). Data shapes differ, so no guarantee of repaired 10-minute soak. Detailed report: [PHASE1_CLOUD_FIFO_MICROBURST_FORENSICS_20261019.md](PHASE1_CLOUD_FIFO_MICROBURST_FORENSICS_20261019.md).

**Phase 1 still OPEN, frozen proposed technical score 81/100 unchanged; previous Windows 396.218s and cloud 427.066s soak failures remain FAILED.** The current stage added useful causal telemetry but no unsafe lossy/buffering optimization and no new 10-minute soak. Next: challenge bounded reader/writer fairness under reproducible subsecond bursts and deliberate downstream stalls without weakening source timestamps/queue or committing unverified gaps; seek private durable original-ledger retention before any institutional 24–72h acceptance.


## XX. 2026-10-19 — One-shot cloud stress campaign: full 3×1,000 + 605.499s watch PASS under opt-in cooperative WS scheduling

A deterministic prebuffered WebSocket fixture exposed a genuine event-loop scheduling starvation: the previous reader could enqueue a burst of 180 frames without yielding until the size-8 FIFO overflowed. A strictly opt-in `await asyncio.sleep(0)` after each successfully captured WS enqueue allowed the writer to progress and accepted the full 180 WS frames plus REST snapshot without changing original receipt stamps, per-record `WAL/FULL` COMMIT, HMAC ACK, queue-capacity refusal or freshness policy. An injected 150ms slow durable verifier still overflowed and was QUARANTINED. The new flag requires all four independently opted-in SQLite cache experiments, is default OFF and never activates REALITY/production.

On a fresh Ubuntu cloud runner the one-shot, dependency-gated workflow https://github.com/coffee2307/econith/actions/runs/39277291146 executed: **Stage 1: 69/69 offline/crash/forensic tests PASS; paired full-cache 240-frame baseline vs cooperative offline PASS** (queue peak 2→1, lag max 133→117ms). **Stage 2: fresh Binance Public 3×1,000 PASS** in 141.52s, original/intent/cursor 3,003, HMAC ACK/checkpoint 2,954; independent read-only SQLite forensic audit 3/3 PASS; FIFO peaks 1/128 for all, max observed admission age 837/1050/943ms, no overflow or orders. **Stage 3: conditional 10-minute segmented watch target reached at 605.499s, 11/11 independent 1000-frame sources PASS**, original/intent/cursor **11,011**, HMAC receiver ACK/checkpoints **10,912**, FIFO peak **1/128**, zero overflows, highest observed per-session lag **669ms**, lowest freshness headroom **4,331ms**, no orders. An independent read-only on-runner audit certified all 11 source/intent hashes/counts/owner closure (11/11 PASS) but deliberately did NOT certify exchange continuity or recompute HMAC signatures.

Aggregate-only, 3-day retention artifacts: finite matrix https://github.com/coffee2307/econith/actions/runs/39277291146/artifacts/12198481310 ; 11-session watch https://github.com/coffee2307/econith/actions/runs/39277291146/artifacts/12199046863 . No original SQLite bytes/HMAC keys archived off ephemeral runner. **Ten inter-session market-history gaps remain UNKNOWN**; the underlying `websockets` library `max_queue=16` may accumulate upstream frames while cooperative reader retains adapter FIFO depth ≤1, so source-to-EventBus measured age is NOT yet a venue-to-source freshness/completeness attestation. Historical Windows 396.218s failed watch and prior cloud 427.066s FIFO-overflow watch remain FAILED as distinct evidence. Read complete handoff at [PHASE1_FAIRNESS_CLOUD_CAMPAIGN_20261019.md](PHASE1_FAIRNESS_CLOUD_CAMPAIGN_20261019.md); the frozen seven-gate explanation at [PHASE1_SCORECARD_AND_CLOUD_CAMPAIGN_20261019.md](PHASE1_SCORECARD_AND_CLOUD_CAMPAIGN_20261019.md).

**Institutional Phase 1 STILL OPEN, proposed 81/100 evidence credit unchanged**: P1-04 external reviewer-approved owner recovery, P1-05 truly continuous verified protected ingress and long soak, P1-06 default verified-only app cutover, P1-07 multi-symbol/OHLCV+24–72h source proof are not closed by bounded, restarted WebSocket sessions. Keep PR #28 DRAFT, no merge, no real execution, no paid external GPU necessary.
