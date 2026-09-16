# RQ1 v3.3 — World đa thang và holdout xác nhận khóa

Phiên bản này bổ sung một pipeline độc lập, không thay v2, World demo hay tự động sửa kết luận báo cáo. Kết quả 2022–2025 chỉ là bằng chứng thăm dò: GLD đạt toàn bộ tiêu chí mạnh, nhưng H1 vẫn chưa được xác nhận vì giai đoạn đó đã được dùng trong phát triển. v3.3 khóa GLD làm tài sản chính và dành 2026-01-01 đến 2026-09-01 làm holdout xác nhận chưa từng được pipeline đánh giá.

## Phạm vi thực hiện

- Một bảng giá nhiều tài sản; lịch phiên riêng, đối chiếu với CSV lịch độc lập trước khi chạy. Không forward-fill giá.
- Dữ liệu công bố theo quốc gia, thời điểm biết thông tin và lần công bố đầu tiên. Không dùng node tổng hợp làm dữ liệu quan sát.
- B0: ridge HAR-style với log bình phương lợi suất 1/5/22 phiên, dự báo căn trung bình bình phương lợi suất tương lai. Đây không phải realized volatility từ dữ liệu trong ngày.
- B1: B0 cộng thông tin vĩ mô trực tiếp. E_static giữ bốn kênh đã chốt: nội địa của tài sản, trung bình quốc tế, chênh lệch nội địa–quốc tế và phân tán giữa quốc gia.
- E1: E_static cộng tác động động của World. Mỗi đổi mới vĩ mô tạo xung nhân quả có chu kỳ bán rã 3/7/21 phiên, sau đó được lan truyền qua mạng. Đặc trưng động giữ cả hướng và độ lớn của cú sốc vì biến động có thể tăng sau cú sốc dương hoặc âm. Hệ số nhớ chỉ học từ train; ensemble thay đổi tham số phục vụ độ nhạy, không diễn giải là xác suất sự kiện.
- Bản US–JP thăm dò ước lượng độ mạnh liên hệ trễ từ riêng từng train fold, có co và giới hạn 0,15. Đây là mạng liên hệ, không phải bằng chứng nhân quả. Cấu hình xác nhận sau này phải thay bằng mạng ngoài mẫu có nguồn độc lập.
- Chọn alpha/trọng số bằng validation, yêu cầu cả MAE/RMSE cải thiện, thắng ít nhất 3/4 khối và khối xấu nhất không giảm quá 1%. Nếu không đạt, tầng bổ sung bị bỏ. Điều này **không bảo đảm** không thua B0 ngoài mẫu.
- Sau khi khóa alpha, trọng số và trạng thái bật/tắt bằng validation, mô hình được khớp lại trên train+validation rồi mới dự báo test. Nhãn test không tham gia lựa chọn hoặc refit.
- C1: xáo trộn khối, giữ cặp cột và ranh giới train/validation/test, chạy lại cùng quy trình chọn tầng. C2: tắt liên kết mạng, giữ trạng thái quốc tế tĩnh.
- Báo cáo riêng tài sản: MAE/RMSE/QLIKE, bootstrap khối theo fold, DM và Holm trên họ asset-fold, độ nhạy khi bỏ những ngày đóng góp tốt nhất.
- Chế độ `confirmatory` chỉ nhận đúng tài sản chính đã đăng ký, đúng một holdout, mạng cố định và đủ số nhãn tối thiểu. H1 chỉ được xác nhận khi mọi tài sản chính đồng thời: E1 thấp hơn B0 và trung vị C1 trên MAE/RMSE, khoảng tin cậy 95% của chênh lệch E1−B0 nằm hoàn toàn dưới 0, và không quá 5% đối chứng C1 tốt bằng E1 trên cả hai sai số.

## Cài đặt và kiểm thử

Dùng môi trường Python của dự án có numpy, pandas và scipy:

```powershell
python -m unittest tests.test_rq1_v3 -v
python -m unittest tests.test_rq1_v2 tests.test_rq1_innovations -v
```

Cài lịch giao dịch dùng riêng cho nghiên cứu:

```powershell
python -m pip install -r requirements-research.txt
```

Các fixture là dữ liệu nhân tạo để kiểm tra phần mềm, không được dùng làm bằng chứng nghiên cứu.

## Chuẩn dữ liệu cần chuẩn bị

`market.csv`: `asset,time,adjusted_close`. Thời điểm có offset, chính là lúc giá đóng cửa đã biết. Một dòng/phiên. Phải ghi rõ nguồn, điều chỉnh chia tách/cổ tức và ngày tải; việc chỉ đặt tên adjusted_close không chứng minh giá đã được điều chỉnh đúng.

`sessions.csv`: `asset,time`, lấy độc lập từ lịch sàn cho đúng khoảng nghiên cứu, gồm giờ đóng cửa thực tế, ngày nghỉ và phiên rút ngắn. Phải khớp bảng giá hoàn toàn.

`releases.csv`: `country,feature,observation_at,available_at,value,unit,source,quality`.

- feature thuộc `interest_rate,inflation,unemployment,gdp_growth`; unit=`fraction`, quality=`observed`.
- available_at là lúc thông tin thật sự có thể sử dụng, không phải kỳ quan sát. Khi chỉ biết ngày công bố, dùng thời điểm bảo thủ và ghi quy tắc ở provenance.
- Lạm phát và GDP giữa các nguồn phải cùng định nghĩa; không trộn GDP tăng trưởng quý thường với tăng trưởng năm hóa. Chuyển đổi phải dùng dữ liệu đã biết tại thời điểm đó.
- Đủ bốn biến cho từng quốc gia. Bản sửa đổi giữ lại trong nguồn lưu trữ nhưng pipeline này sử dụng vintage đầu tiên mỗi kỳ.
- Mã không xác thực tuyên bố nguồn/quality: dữ liệu lịch sử vẫn cần nghiệm thu độc lập.

Ma trận `network` có hàng là quốc gia nhận, cột là quốc gia truyền, đường chéo 0, không âm, tổng hàng <=1. Với `network_mode=fixed`, mạng phải có nguồn và được công bố trước train đầu tiên. Với `network_mode=train_association`, mã chỉ dùng train để đo liên hệ trễ tuyệt đối, co kết quả và ghi ma trận từng fold vào `metrics.json`; không gọi mạng này là nhân quả. Exposure tài sản là trọng số không âm tổng 1 theo thứ tự countries, phải chốt trước khi xem test; không gọi trọng số thiết kế là exposure quan sát.

## Mở rộng phạm vi theo dữ liệu

Thí điểm US–JP hiện có 11 tài sản thuộc các nhóm cổ phiếu, trái phiếu, vàng, dầu, USD và tiền mã hóa. ETF niêm yết ở Mỹ vẫn theo lịch Mỹ và có tác động tỷ giá, không đồng nhất với chỉ số nội địa. Vàng, dầu và tiền mã hóa dùng trọng số đều Mỹ–Nhật như một thiết kế độ nhạy đã công bố trước; không diễn giải trọng số này là mức phơi nhiễm kinh tế quan sát được.

Tải dữ liệu thị trường và dữ liệu vĩ mô:

```powershell
python -m KHKT_Evaluation.rq1_v3.fetch_market --start 2008-01-01 --end 2026-01-01
$env:FRED_API_KEY="..."
python -m KHKT_Evaluation.rq1_v3.fetch_macro --start 2008-01-01 --end 2026-01-01
Remove-Item Env:FRED_API_KEY -ErrorAction SilentlyContinue
```

Giá điều chỉnh của ETF lấy từ endpoint chart Yahoo Finance và được đối chiếu với lịch NYSE độc lập. Đây không phải giao diện nghiên cứu được cam kết ổn định; metadata ghi nguồn và ngày tải. Crypto lấy nến ngày công khai từ Binance. Nếu Binance bị chặn theo khu vực, có thể tạo bộ chín ETF trước:

```powershell
python -m KHKT_Evaluation.rq1_v3.fetch_market --start 2008-01-01 --end 2026-01-01 --assets SPY QQQ IWM EWJ TLT LQD GLD USO UUP
```

Khi loại tài sản khỏi bước tải, tạo bản sao cấu hình và xóa đúng tài sản đó khỏi `assets`. Không sửa cấu hình gốc sau khi xem kết quả.

`config.template.json` cố ý chưa chạy được: nguồn, lịch sử sử dụng dữ liệu, ma trận và exposure chưa được điền. Không thay các mục này bằng số tùy ý để vượt kiểm tra. Mốc fold là gợi ý cấu trúc cho thăm dò, không phải holdout mới.

```powershell
python -m KHKT_Evaluation.rq1_v3.run --market datasets/v3/market.csv --sessions datasets/v3/sessions.csv --releases datasets/v3/releases.csv --config KHKT_Evaluation/rq1_v3/config.local.json --output KHKT_Evaluation/results_rq1_v3/local_001
```

Lệnh chạy cho bộ US–JP đã định sẵn:

```powershell
python -m KHKT_Evaluation.rq1_v3.run --market datasets/rq1_v3/market.csv --sessions datasets/rq1_v3/sessions.csv --releases datasets/rq1_v3/releases.csv --config KHKT_Evaluation/rq1_v3/config.us_jp.exploratory.json --output KHKT_Evaluation/results_rq1_v3/us_jp_v31_001
```

Output không được tồn tại trước; xuất metrics.json, predictions.csv cùng hash đầu vào và cấu hình. `h1_evidence` báo riêng tiêu chí số, khoảng tin cậy và vị trí trong phân phối C1 theo tài sản. `h1_exploratory_supported` không đồng nghĩa `h1_confirmed`: dữ liệu đã xem trong phát triển chỉ có thể cung cấp bằng chứng thăm dò. Giữ nguyên các kết quả thua; không ghi đè bằng lần chạy được lựa chọn.

## Chạy giao thức xác nhận GLD đã khóa

Không sửa `config.us_jp.confirmatory_gld.json` sau khi xem kết quả. Dữ liệu mới được lưu ở thư mục riêng để không ghi đè bộ thăm dò. Cấu hình chỉ dùng GLD, giữ nguyên thuật toán và siêu tham số v3.2, dùng dữ liệu đến 2025 làm phát triển và chỉ đánh giá một lần trên holdout 2026.

```powershell
python -m KHKT_Evaluation.rq1_v3.fetch_market `
  --start 2008-01-01 `
  --end 2026-09-01 `
  --assets GLD `
  --directory datasets/rq1_v3_confirmatory_gld

$env:FRED_API_KEY="..."
python -m KHKT_Evaluation.rq1_v3.fetch_macro `
  --start 2008-01-01 `
  --end 2026-09-01 `
  --output datasets/rq1_v3_confirmatory_gld/releases.csv
Remove-Item Env:FRED_API_KEY -ErrorAction SilentlyContinue

python -m KHKT_Evaluation.rq1_v3.run `
  --market datasets/rq1_v3_confirmatory_gld/market.csv `
  --sessions datasets/rq1_v3_confirmatory_gld/sessions.csv `
  --releases datasets/rq1_v3_confirmatory_gld/releases.csv `
  --config KHKT_Evaluation/rq1_v3/config.us_jp.confirmatory_gld.json `
  --output KHKT_Evaluation/results_rq1_v3/us_jp_gld_confirmatory_001
```

Nếu `h1_confirmed=true`, kết luận được giới hạn ở GLD, thiết kế US–JP và holdout đã khóa; không suy rộng sang mọi tài sản. Nếu bằng `false`, giữ nguyên kết quả và báo cáo H1 chưa được xác nhận. Dù kết quả nào, không dùng chính holdout này để sửa mô hình rồi gọi lần chạy sau là xác nhận.

## Những cổng nghiên cứu chưa hoàn tất

1. Nghiệm thu vintage, giá và mạng quốc tế thật; thêm adapter theo nguồn sau khi kiểm tra quyền sử dụng và khả năng lấy lịch sử.
2. World hiện là surrogate mạng trạng thái đa thang, chưa phải kernel đa tác nhân đầy đủ, chưa có vòng học giả thuyết tự động hay hiệu chỉnh xác suất thiên nga đen. Giao thức GLD khóa mạng lan truyền bằng 0 nên chỉ xác nhận giá trị của trạng thái và xung World; muốn xác nhận hiệu ứng lan truyền giữa quốc gia phải bổ sung mạng thương mại/tài chính bên ngoài.
3. Các tầng vẫn dùng chung validation để khóa lựa chọn, nên có thể còn thiên lệch lựa chọn dù bước refit chỉ dùng dữ liệu có trước test. Cần nested walk-forward và giới hạn ngân sách tìm kiếm trước xác nhận.
4. Bootstrap hiện có điều kiện trên dự báo đã khớp, block mặc định bằng horizon có thể chưa đủ cho phụ thuộc dài; cần phân tích độ nhạy block đã chốt trước. DM theo fold và Holm không sửa được thiên lệch do đã xem test nhiều lần.
5. Chưa có C3 đầy đủ hoặc suy luận tổng hợp phụ thuộc chéo tài sản. Giao thức xác nhận hiện chỉ đăng ký GLD; không dùng ngày lịch sử đã thăm dò làm bằng chứng xác nhận mới và không suy rộng kết quả GLD sang toàn bộ thị trường.
6. Chỉ sau khi chốt dữ liệu và giao thức mới chạy benchmark thị trường, đánh giá E1 với B0, B1, E_static và phân phối C1. Chỉ cập nhật báo cáo khi có kết quả thực.
