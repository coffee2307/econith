# RQ1 v4 — Giá trị tăng thêm của World trên B1

RQ1 v4 là nhánh phát triển thăm dò riêng, không thay đổi kết quả holdout khóa của v3.3. Mục tiêu mới là kiểm tra nghiêm ngặt hơn: tín hiệu do World tạo ra có cải thiện dự báo sau khi mô hình đã có cả dữ liệu thị trường và dữ liệu vĩ mô quan sát trực tiếp hay không.

## Thiết kế lồng nhau

- B0: chỉ dùng lịch sử biến động thị trường.
- B1: B0 cộng các mức vĩ mô quan sát trực tiếp.
- E_static: B1 cộng trạng thái quốc tế được World chuyển đổi.
- E1: E_static cộng xung động đa thang do World tạo ra.
- C1: bắt đầu từ cùng B1 nhưng xáo trộn riêng các đặc trưng World trong từng phần train/validation/test.
- C2: giữ trạng thái World nhưng tắt liên kết lan truyền động.

Các tầng vĩ mô dùng hiệu chỉnh nhân theo tỷ lệ để phù hợp với đại lượng biến động luôn dương. Đặc trưng World được tương tác với mức căng thẳng và xu hướng biến động thị trường; mọi tham số chuẩn hóa chỉ lấy từ train. Nếu validation không đạt cổng MAE/RMSE và độ ổn định theo khối, tầng mới quay về đúng baseline trước đó.

RQ1 v4 chỉ được chạy trên các fold kết thúc trước `2026-01-01`. Mã chủ động chặn mọi cấu hình đọc holdout GLD 2026 đã dùng trong v3.3.

## Chạy kiểm thử

```powershell
python -m unittest tests.test_rq1_v4 tests.test_rq1_v3 tests.test_rq1_v2 tests.test_rq1_innovations -v
```

## Chạy trên dữ liệu phát triển cũ

```powershell
python -m KHKT_Evaluation.rq1_v4.run `
  --market datasets/rq1_v3/market.csv `
  --sessions datasets/rq1_v3/sessions.csv `
  --releases datasets/rq1_v3/releases.csv `
  --config KHKT_Evaluation/rq1_v4/config.us_jp.exploratory.json `
  --output KHKT_Evaluation/results_rq1_v4/us_jp_nested_001
```

Một tài sản chỉ được ghi là có bằng chứng thăm dò mạnh khi E1 thấp hơn B0, B1 và trung vị C1 trên cả MAE/RMSE; hai khoảng tin cậy E1−B0 và E1−B1 nằm hoàn toàn dưới 0; đồng thời không quá 5% đối chứng C1 tốt bằng E1. Kết quả này vẫn không phải xác nhận mới vì toàn bộ dữ liệu trước 2026 đã được xem trong phát triển.
