# ECONITH — P1-07 bounded point-in-time decision outcome scorecard

**Ngày:** 20/10/2026 · **Bài toán:** Đo *hướng đi của giá sau một quyết định* trên dữ liệu được quan sát độc lập sau thời điểm quyết định. **KHÔNG** báo cáo đây là lợi nhuận/portfolio backtest hoặc quyết định của một LLM đã chạy thật.

## Khác gì những bước trước

PR #40 tạo bộ bằng chứng point-in-time và quorum technical / bull / bear / risk / portfolio, ràng buộc dữ liệu SHA của original, từ chối self-hashed forged evidence và không có API order. **PR này nối thêm khâu đánh giá hậu nghiệm có điều kiện:** người sử dụng đưa hai thư mục bằng chứng Spot hữu hạn, được thu tại **hai thời điểm khác nhau**. Cả hai phải qua kiểm toán raw SHA/quality/market status độc lập. Dữ liệu làm tín hiệu phải được ghi nhận trước thời điểm quyết định; dữ liệu dùng để chấm kết quả phải được ghi nhận sau quyết định và trước thời điểm chấm.

Điểm so sánh là đóng cửa nến đầu tiên kế tiếp có `close_time > decision_ms`, lấy từ dữ liệu thu sau đó; một nến giá chưa đóng hoặc nến bị thiếu làm thử nghiệm **REFUSED**, không nội suy. Cấm gọi lại cùng một archive để giả bộ có outcome tương lai. Hai archive phải có phần nến quá khứ trùng nhau khớp **toàn bộ trường thị trường**; nếu xuất hiện revision/khác nhau giữa dữ liệu trước và sau thì từ chối, không viết lại lịch sử.

## Hai hướng so sánh

- **Advisory**: Buy/Overweight = hướng tăng (+1); Sell/Underweight = hướng giảm (-1); Hold hoặc risk VETO = không dự đoán hành động. Chỉ cho phép đủ năm vai trò, timestamp và evidence SHA hợp lệ, original vẫn phải kiểm toán lại.
- **Baseline**: moment của **hai nến đóng được biết trước quyết định**; last close lớn hơn previous close = +1, ngược lại = -1, bằng nhau = 0. Đây là baseline nguội đơn giản để kiểm tra xem agent có hơn quy tắc cơ bản hay không; chưa phải chiến lược được chọn/huấn luyện.
- Trả về Boolean *direction correct* (hoặc null khi không có hướng/giá đi ngang) và mức biến động tương đối của tài sản chưa tính chi phí. **Không** phát sinh fill, quantity, vốn, sổ lệnh, funding, spread, commission, cash ledger hay PnL. Đặc biệt, đánh giá Sell theo hướng giảm không chứng minh khả năng short một tài sản Spot.

## Tính tái lập và sử dụng

Chức năng `ci.phase1_pit_decision_scorecard.evaluate` lấy decision archive, một follow-up archive, symbol, interval, `decision_ms`, năm báo cáo nghiên cứu không có lệnh và `observed_by_ms`. `write_sealed_evidence` ghi `finite_scorecard.json` chỉ vào thư mục **mới**, dùng fsync, và `independently_reaudit_scorecard` mở lại **cả hai bộ original** để tái tính toàn bộ điểm. Cấm sửa đổi evidence hoặc cờ `net_profit_after_costs_computed` rồi qua kiểm toán.

Có CLI:
```bash
python -m ci.phase1_pit_decision_scorecard \
  --allow-research-scorecard --acknowledge-not-portfolio-pnl \
  --decision-root /private/epoch_before \
  --followup-root /private/epoch_after \
  --output-dir /private/scorecard_new \
  --symbol BTCUSDT --interval 1m \
  --decision-ms 1700000001000 \
  --observed-by-ms 1700000662000 \
  --reports-json /private/advisory_reports.json
```
Các thời điểm và đường dẫn ở trên **minh họa cấu trúc**, không phải bằng chứng dữ liệu venue đã có sẵn. Mọi bài kiểm thử CI dùng fixture sinh trực tiếp không qua mạng và không có LLM.

## Còn thiếu trước khi nói có lợi nhuận giao dịch

Chưa có dữ liệu dài đủ để lấy đánh giá thống kê về Sharpe, realized PnL, nhiều trạng thái thị trường, khả năng thực thi, chọn tài sản không thiên lệch, phí/slippage/funding/capacity hoặc kết quả của một model agent đã chạy thật. Cũng chưa chứng minh vintage listing/delisting lịch sử hay venue-wide data completeness; original SHA là bằng chứng toàn vẹn dữ liệu **cục bộ**, không phải Binance-signed receipt. Một phiên đóng cửa kế tiếp không thay thế backtest walk-forward hoặc soak tích hợp. **Phase 1 vẫn 81/100 provisional**, điều kiện dài hạn thực hiện theo kế hoạch chiến dịch liên phase đã duyệt.
