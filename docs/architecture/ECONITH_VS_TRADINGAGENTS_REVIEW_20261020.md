# ECONITH và TradingAgents — Đối chiếu mã nguồn và lộ trình tích hợp (20/10/2026)

## Bằng chứng nguồn và phạm vi

- **TradingAgents upstream:** [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents), giấy phép Apache-2.0; phát hành chính thức [v0.7.0 ngày 16/10/2026](https://github.com/TauricResearch/TradingAgents/releases/tag/v0.7.0). Mã đã đọc: `tradingagents/backtest.py`, `tradingagents/dataflows/date_window.py`, `tradingagents/agents/managers/portfolio_manager.py`, `tradingagents/agents/structured.py`, `tradingagents/agents/schemas.py`, `tradingagents/graph/trading_graph.py` (tag v0.7.0).
- **ECONITH:** `coffee2307/econith`, nghiên cứu trên chuỗi PR Draft #28–39. Điểm Phase 1 81/100 mang tính **bằng chứng kỹ thuật tạm tính**, không phải điểm sinh lời hoặc tỷ lệ thắng dự báo.
- **Hạn chế phân tích:** Không có một benchmark giao dịch thật cùng universe, vốn, chi phí, độ trễ, giai đoạn out-of-sample và kiểm soát rủi ro cho cả hai. **Không có căn cứ gọi một trong hai là bot profitable hơn**.

## Giống nhau

Cả hai đều mô tả quy trình phân tích thị trường, quản trị rủi ro, các chế độ mô phỏng và nhu cầu đánh giá từ dữ liệu trước khi giao dịch. Cả hai đều có ý tưởng sử dụng nhiều loại tín hiệu/nguồn dữ liệu và lưu vết quyết định. Nhưng lớp giao tiếp và hình thức kiểm chứng hoàn toàn khác nhau.

## Những mặt khác nhau (đánh giá giới hạn theo source code đã xem)

| Tiêu chí | TradingAgents v0.7.0 | ECONITH hiện tại | Kết luận thực dụng |
|---|---|---|---|
| Hệ thống AI phân tích và phản biện | Có analyst cơ bản/kỹ thuật/tin tức/tâm lý, bull/bear debate, trader, risk, portfolio manager, LangGraph | Có hạ tầng định lượng/risk/engine, chưa có lớp hội đồng LLM chuẩn hóa như vậy | **TradingAgents hơn** về quy trình giải thích/triển khai tác nhân |
| Provider và giao diện | Nhiều LLM provider, tier-model; CLI, báo cáo HTML, checkpoint | API/dashboard và mô phỏng khá lớn nhưng đường market verified tách rời runtime legacy | **TradingAgents hơn** về dễ bắt đầu nghiên cứu định hướng cổ phiếu |
| Điểm-thời gian / look-ahead | `date_window.py` bảo vệ cửa sổ; báo cáo v0.7 backtest từ giá đóng cửa tiếp theo; kết quả phải có published-at | Original SHA/HMAC, snapshot, epoch UNKNOWN, original→ACK; OHLCV hữu hạn có cutoff nhưng thiếu historical vintage đầy đủ | Cả hai mạnh ở những lớp khác nhau; **không thể phong thắng tuyệt đối** |
| Dữ liệu streaming Binance Spot crypto | Không phải trọng tâm upstream; có crypto market/sentiment | REST/WSS Spot, journal nguồn quay vòng, FIFO 64/128, HMAC receiver, observer, checkpoint | **ECONITH có bằng chứng kiểm thử sâu hơn** tại đường lưu dữ liệu crypto, không có nghĩa profit hơn |
| Backtest lợi nhuận sau phí, khớp lệnh | `backtest.py` tuyên bố rõ *decision quality*, **không phải portfolio simulator**, không có fill/cash ledger trong module này | Hướng kiến trúc đã có mô phỏng/CCXT/quant layer nhưng chưa có bằng chứng lợi nhuận live được kiểm toán | **Chưa có người thắng** về lợi nhuận thực tế; không lấy alpha rating làm realized PnL |
| Giao dịch thật/độ tin cậy 24h | README mô tả simulated exchange và lưu ý research-only | Live capital giữ tách biệt; Phase1 chưa thoát legacy default, soak/fault chưa đủ | **Không hệ thống nào được chứng nhận an toàn/profitable cho live** trên những bằng chứng này |
| Tốc độ nghiên cứu ý tưởng định tính | Nhiều vai trò LLM phối hợp, báo cáo có thể xuất nhanh sau khi cấu hình provider | Nhiều chốt an toàn dữ liệu và CI làm tiến độ chậm hơn | **TradingAgents có lợi thế về UX nghiên cứu**, ECONITH có lợi thế kiểm định nguồn crypto đang xây |
| Đánh giá portfolio thật | Portfolio context và risk team; `backtest.py` vẫn không ghi fill/cash ledger | Có execution/cost simulation đang phát triển, chưa chứng nhận chất lượng out-of-sample | **Không thể suy diễn** hệ thống nào hơn về quản trị tiền thật |

## Bài học áp dụng vào ECONITH theo độ ưu tiên

1. **P1-07 PIT/vintage contract** — một decision tại thời điểm `t` không được đọc OHLCV, tin tức, snapshot market status hay ký ức agent được quan sát **sau `t`**, kể cả nếu nội dung ghi ngày quá khứ. Phân biệt candle-close timestamp với `source_observed_after_ms`. Với nguồn không lưu `first observed/publication vintage` thì không đủ tư cách đánh giá backtest lịch sử. Không sử dụng listing status hiện tại để suy ra tình trạng niêm yết trong quá khứ.
2. **Structured analyst/risk/portfolio advisory** — các vai trò technical, bull, bear, risk, portfolio phải cùng dẫn đến một provenance SHA của bundle đã kiểm toán; đủ quorum, schema, evidence timestamp; mọi cờ VETO giữ lại. Đầu ra là *research rating không thực thi*, không có price/quantity/API order.
3. **Decision journal + model/version/config lineage** — sau khi có LLM thật, lưu model/provider/prompt hash, token/cost, bằng chứng đầu vào, output, thời điểm ra quyết định và lỗi. LLM không được tự ý truy cập tool/broker; fail-closed khi không có schema hay nguồn.
4. **Backtest/walk-forward khách quan** — đánh giá theo universe định nghĩa trước, time split, Purged CV/embargo nếu cần, model costs, spreads/slippage/funding, turnover/drawdown, randomized baselines, capacity. *Decision rating benchmark* tách biệt khỏi *portfolio PnL*. LLM đa agent có thể chỉ tăng chi phí, latency và overfit.
5. **Kiểm thử dài hạn tích hợp sau** — tuân thủ `docs/architecture/INTEGRATED_DEFERRED_SOAK_MASTER_PLAN_20261020.md`. Không gộp tiêu chuẩn nghiệm thu vào số lượng agent hay CI pass.

## Những gì PR nghiên cứu này thực sự thêm

- `ci/phase1_pit_multiagent_advisory.py` đưa ra contract độc lập, chạy offline, tái sử dụng bộ kiểm toán toàn bộ raw 600 nến Binance đã có. Không có network/LLM/order. Chỉ nhận một trong 3 cặp BTCUSDT/ETHUSDT/BNBUSDT, khung 1m/5m, cảnh báo tuổi dữ liệu hơn 10 phút, cấm source vintage sau decision, đòi `TRADING` **tại thời điểm capture** và từ chối khi raw SHA sai.
- Ràng buộc typed roles technical/bull/bear/risk/portfolio, source evidence digest, available-at timestamp, độ dài nội dung; cố ý từ chối các trường order, quantity, báo cáo trùng vai trò, dự báo về tương lai hoặc quyết định thiếu dữ liệu. Risk VETO dẫn tới WITHHELD; các rating khác **không được thực thi**.
- Không tích hợp LangGraph hay gọi LLM thật. Không cấp P1-07 10/10, vì listing/delisting historical vintage, đa tài sản/timeline dài hạn và kiểm định PnL chưa xong. **Phase1 vẫn 81/100, OPEN.**

## Kết luận về trading thực tế

Với dữ liệu hiện có, **TradingAgents tốt hơn như công cụ đồng nghiệp AI nghiên cứu quyết định**; **ECONITH phù hợp hơn làm nền móng một hệ thống định lượng crypto có dữ liệu xác minh, kiểm soát thực thi và bằng chứng crash**. Nhưng không thể bảo rằng ECONITH hoặc TradingAgents giao dịch live có tỷ lệ thắng, Sharpe hay lợi nhuận thực tế cao hơn. Muốn chấm đúng cần chạy thử cùng kỳ, phí, latency, vốn, độ biến động và áp dụng quy trình thống kê độc lập. Không gửi lệnh thật trước khi những cổng an toàn và nghiệm thu hoàn tất.

_Thông tin upstream kiểm tra theo tag v0.7.0 phát hành 16/10/2026. Trình bày căn cứ tại đây không bao hàm bảo đảm lợi nhuận._
