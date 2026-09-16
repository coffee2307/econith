# RQ1 v2 — bản thử nghiệm độc lập

## World theo tác động riêng của lần công bố

Các lần chạy 3/7/14 ngày cho thấy dữ liệu công bố trực tiếp có tín hiệu nhẹ ở
chân trời 14 ngày, còn replay World tích lũy làm kết quả kém ổn định. Bản tiếp
theo chỉ giữ năm tác động World: biến động, dòng lệnh, thanh khoản, lạm phát và
niềm tin. Mỗi tác động là chênh lệch giữa hai kịch bản có cùng trạng thái hiện
tại: một nhánh nhận lần công bố mới, nhánh kia giữ giá trị trước đó. Hai nhánh
được phát 14 ngày bằng cùng tác nhân và không đọc công bố tương lai.

Mô hình chỉ được bật khi giảm ít nhất 0,5% MAE kiểm định và thắng B0 trong cả ba
đoạn thời gian liên tiếp. Mức hiệu chỉnh được chọn trong 0,25/0,5/1 để hạn chế
phản ứng quá mạnh. Các quy tắc này được áp dụng giống nhau cho E1 và C1.

Tạo cấu hình local từ `config.causal.example.json`, chép nguyên
`release_provenance` từ cấu hình local hiện tại, sau đó chạy:

```powershell
python -m unittest tests.test_rq1_v2 tests.test_rq1_innovations -v
python -m KHKT_Evaluation.rq1_v2.run --market datasets/features/BTCUSDT_features.parquet --releases datasets/macro_releases.csv --config KHKT_Evaluation/rq1_v2/config.causal.local.json --causal-world-preset --output KHKT_Evaluation/results_rq1_v2/causal_14d
```

Hai fold mới bắt đầu từ năm 2025 để không tiếp tục chọn thiết kế bằng kết quả
2023–2024. Chúng vẫn được gọi là thăm dò vì dữ liệu có thể đã xuất hiện trong
các thử nghiệm V1. Phải giữ cả hai fold và mọi kết quả, kể cả khi E1 thua.

## Nhánh nâng cấp tín hiệu công bố

Giữ nguyên `local_001`. Chạy cấu hình nâng cấp bằng cờ tùy chọn:

```powershell
python -m unittest tests.test_rq1_v2 tests.test_rq1_innovations -v
python -m KHKT_Evaluation.rq1_v2.run --market datasets/features/BTCUSDT_features.parquet --releases datasets/macro_releases.csv --config KHKT_Evaluation/rq1_v2/config.local.json --innovation-preset --output KHKT_Evaluation/results_rq1_v2/innovations_3d
```

Bỏ cờ `--innovation-preset` để giữ cách tính cũ. Nhánh mới bổ sung thay đổi tại
ngày công bố, chuẩn hóa bằng lịch sử trước đó và bộ nhớ suy giảm 3/7/14 ngày.
Đây là độ bất thường so với quá khứ, KHÔNG phải bất ngờ so với khảo sát thị trường.
B1 được bổ sung cùng các tín hiệu công bố để kiểm tra giá trị riêng của World.
World chạy chậm bằng 1/30 nhịp gốc mỗi ngày; đây là giả định thăm dò, chưa phải
hệ số đã hiệu chỉnh. Nhánh phản thực tế giữ các quan sát đầu tiên suốt một fold,
không phải mô phỏng phản thực tế riêng cho từng sự kiện và không chứng minh nhân quả.

Có thể chạy thêm `--horizon-days 7` hoặc `--horizon-days 14`, mỗi lần dùng thư mục
output mới. Phải lưu và báo cáo tất cả chân trời, không chỉ giữ lần thắng. Không
đổi H1 sau khi thấy kết quả. Các giai đoạn đã xem vẫn là thăm dò. `provenance.config`
lưu cấu hình thực tế sau áp dụng cờ; `config_sha256` chỉ là dấu vân tay tệp đầu vào.

Chưa triển khai mô hình học chính sách, LLM tự kiểm định giả thuyết, mở rộng nhiều
quốc gia, đường cơ sở HAR hoặc hiệu chỉnh động học tự động. Không gọi bản nâng cấp
này là hệ thống dự báo thiên nga đen. Không thay đổi demo, cờ LLM hoặc báo cáo cũ.
Sai số kiểm định của mọi alpha được lưu trong `selected.*.candidates`, kể cả khi
quay về B0. Tập test không tham gia chọn alpha.

Nguồn phương pháp phân chia thời gian:
https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html

Trạng thái: đã triển khai đường chạy thử nghiệm và kiểm thử phần mềm; chưa
nghiệm thu toàn bộ kế hoạch nâng cấp và chưa có kết quả trên dữ liệu thực.
Không thay thế kết quả V1, báo cáo KHKT, giao diện hoặc cấu hình LLM.

## Chạy trên máy local

Từ thư mục gốc repository, dùng môi trường Python đã cài các phụ thuộc của dự án
(numpy, pandas, pydantic và pyarrow để đọc parquet). Kiểm thử mới không cần pytest:

```bash
python -m unittest tests.test_rq1_v2 -v
python -m KHKT_Evaluation.rq1_v2.audit datasets/features/BTCUSDT_features.parquet
```

Trước hết gửi kết quả audit nếu chưa có lịch công bố. Không thể lấy cột macro đã
điền lặp trong parquet rồi tự coi ngày đổi giá trị là ngày công bố thật.

Tạo lịch công bố ban đầu trực tiếp từ FRED/ALFRED bằng khóa FRED cá nhân:

```bash
$env:FRED_API_KEY="khóa-của-Coffee"
python -m KHKT_Evaluation.rq1_v2.fetch_fred_releases --start 2019-01-01 --end 2026-07-26
```

Không gửi khóa API trong kết quả. Công cụ dùng `output_type=4`, tức giá trị ở lần
công bố đầu tiên; ngày khả dụng được đặt sang 00:00 UTC ngày kế tiếp vì API chỉ
cung cấp ngày, không cung cấp giờ. Bốn chuỗi là FEDFUNDS, CPIAUCSL (tăng CPI so
với cùng kỳ), UNRATE và GDPC1 (tốc độ tăng theo năm của quý). Giá trị được đổi từ
phần trăm sang tỷ lệ. Do FRED không cho dùng phép biến đổi phía máy chủ cùng
`output_type=4`, công cụ tải giá trị gốc với `units=lin`, lấy thêm 12 tháng CPI và
3 tháng GDP làm kỳ gốc, rồi tự tính mức tăng từ các lần công bố ban đầu. Tệp
metadata đi kèm ghi lại quy tắc và nguồn.

Nếu FRED trả lỗi, công cụ chỉ in mã HTTP và thông báo của máy chủ; URL chứa khóa
không được đưa vào lỗi. Lỗi `api_key is not registered` yêu cầu tạo hoặc kích hoạt
khóa mới trong tài khoản FRED. Không dán khóa vào báo cáo, issue hoặc cuộc trò chuyện.

Tạo bản sao `config.example.json` trên máy, điền `release_provenance` bằng nguồn
ngày công bố, cách xử lý phiên bản sửa đổi, đơn vị và hạn chế dữ liệu. Các ngày
trong cấu hình chỉ là ví dụ thăm dò; điều chỉnh theo phạm vi có dữ liệu trước khi
chạy, không lựa chọn theo kết quả sai số. Cấu hình mặc định cố ý bị chặn khi chưa
điền nguồn. Các thí nghiệm đã xem trước đây không phải tập kiểm tra mới.

```bash
python -m KHKT_Evaluation.rq1_v2.run --market datasets/features/BTCUSDT_features.parquet --releases datasets/macro_releases.csv --config KHKT_Evaluation/rq1_v2/config.local.json --output KHKT_Evaluation/results_rq1_v2/local_001
```

`--output` phải chưa tồn tại. Gửi lại `metrics.json`, `predictions.csv` và kết quả
kiểm thử. `controls.npz` giữ toàn bộ dự báo ngẫu nhiên để kiểm tra sâu hơn.
Không tự chuyển nội dung này vào báo cáo hiện hành.

## Định dạng đầu vào

Market: `ts_ms` tăng dần và duy nhất, giá dương ở `price`. Các cột macro trong
market không được sử dụng. Nhãn là độ lệch chuẩn của lợi suất tương lai, không
phải dự báo hướng giá; V2 mặc định đo lợi suất ngày và chân trời ba ngày. Vì khác
tần suất V1, không so trực tiếp trị số MAE giữa hai phiên bản.

Release CSV phải có:

| Cột | Nội dung |
|---|---|
| available_at | Thời điểm công bố thực sự, gồm múi giờ |
| observation_at | Thời điểm/kỳ quan sát được công bố |
| feature | interest_rate, inflation, unemployment hoặc gdp_growth |
| value | Số dạng tỷ lệ: 5% = 0.05; không suy đoán đơn vị theo độ lớn |
| unit | fraction |
| source | Nguồn truy vết được của giá trị và thời điểm công bố |

Inflation là tốc độ tăng CPI, không phải mức chỉ số CPI. Cần khai báo trong nguồn
GDP là tăng trưởng năm hay quý; V2 không tự quy đổi hai khái niệm này. Các bản sửa
đổi phải được gắn ngày chúng thực sự xuất hiện, không gán giá trị sửa đổi vào ngày
công bố đầu tiên. Người dùng chịu trách nhiệm cung cấp nguồn đúng; kiểm tra cột
không thể xác nhận tính xác thực của nguồn.

## Thiết kế đã triển khai

- Train/validation/test theo thời gian thực, loại nhãn vượt ranh giới. Chỉ validation
  chọn Ridge; test không tham gia chọn tham số. Các test fold không chồng nhau.
- Khởi tạo replay riêng cho từng fold. Giữ trạng thái giữa các ngày, chỉ cập nhật
  quan sát khi có bản công bố mới. Không đọc kết quả Quant hoặc gọi LLM.
- Tái sử dụng `CentralBankModel`, `SentimentModel` và `macro_to_micro`; phạm vi USA.
  Đây là replay rút gọn, không phải toàn bộ kernel đa tác nhân 150 quốc gia.
- Năm đặc trưng mức và năm thay đổi. Không thêm các biến spread/regime trùng
  thông tin tuyến tính với đầu vào hiện có. Biến hằng được báo cáo, không tự bịa nhiễu.
- B0 giữ dự báo độ biến động quá khứ; B1 thêm dữ liệu quan sát; E1 thêm World;
  E1_plus thêm cả hai; E_static bỏ duy trì trạng thái để kiểm tra giá trị động học.
- Chọn giữa Ridge và không hiệu chỉnh B0 bằng validation. Chưa có cổng kích hoạt
  theo từng sự kiện; tránh đưa ngưỡng chưa kiểm định vào giao thức chính.
- C1 xáo trộn theo khối, riêng từng đoạn, giữ các cột cùng hàng và huấn luyện lại.
  Báo cáo phân phối của 200 lần. Đây là đối chứng phá cấu trúc, không phải dự báo
  có thể triển khai; tỷ lệ C1 thắng không được gọi là p-value chính xác.
- DM theo fold, khoảng tin cậy MAE/RMSE theo khối, lưu dự báo và dấu vân tay nguồn.
  Không tự tuyên bố H1 được chứng minh chỉ từ bảng điểm.
- Danh sách events trong cấu hình dùng name/start/end; chỉ chấm trên dự báo OOS.
  Khai báo riêng các khoảng trước/trong/sau nếu cần. Không sửa cửa sổ sau khi xem kết quả.

## Các phần chưa nghiệm thu

1. Dữ liệu gốc và lịch công bố không có trong môi trường triển khai; chưa chạy thật.
2. Hiệu chỉnh lại tham số động học theo train, khởi tạo trạng thái lịch sử có nguồn
   và mở rộng nhiều quốc gia chưa thực hiện. Trạng thái chưa quan sát vẫn dùng mặc
   định mô hình; không được trình bày như trạng thái lịch sử đã kiểm chứng.
3. Cổng kích hoạt theo sự kiện, trọng số tài chính toàn cầu và tích hợp vào kernel
   demo chưa thực hiện. Không thay mặc định mô phỏng để ép E1 tốt hơn.
4. Chưa chuyển EXP_001/004/006/009 và bộ dựng báo cáo V1 sang V2. V2 dùng cùng một
   lần dự báo cho điểm tổng hợp, đối chứng, sự kiện và kiểm định; các báo cáo V1 giữ
   nguyên tại commit gốc 4c1eb8b4cdf756f75b8e4a1e90185c117600cf9d.
5. Chưa chạy được toàn bộ bộ test cũ trong môi trường thiếu pytest. Kiểm thử mới
   dùng unittest và dữ liệu giả được đánh dấu rõ; không tạo số liệu nghiên cứu.

Không gắn tỷ lệ chắc chắn 95% cho các phần trên. Chỉ nghiệm thu sau khi có dữ liệu,
kiểm thử hồi quy và đánh giá độc lập. Kết quả E1 thua vẫn phải được giữ nguyên.

## Kiểm tra khi bàn giao

- 12/12 kiểm thử unittest đạt, gồm chạy lại toàn pipeline và đối chiếu dự báo C1.
- `compileall` và `git diff --check` đạt.
- Bộ test pytest cũ chưa chạy: môi trường không có pytest; nguồn cài đặt hiện tại
  không cung cấp bản yêu cầu. Không tính đây là test đạt hoặc lỗi của mô hình.
- Numpy 2.3.5, pandas 2.2.3, pydantic 2.13.5 trong môi trường kiểm tra. Chưa xác nhận
  tương đương số học với numpy 1.26.4 trong requirements-dev của dự án.
- Bản bàn giao nằm trên nhánh thử nghiệm riêng, chưa nhập vào nhánh đang dùng demo.
