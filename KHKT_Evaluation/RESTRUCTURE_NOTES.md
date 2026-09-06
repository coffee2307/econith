# Ghi chú tái cấu trúc báo cáo ECONITH-KHKT

Nguồn: `c:\Users\abc23\Downloads\ECONITH-KHKT.docx`  
Đầu ra: `KHKT_Evaluation/ECONITH_KHKT_BaoCao.docx`  
Nguyên tắc: giữ văn phong và thuật ngữ; chỉ sắp xếp, gộp, rút gọn; không viết lại kiểu AI.

---

## Ánh xạ chương cũ → mới

| Cũ | Mới | Ghi chú |
|----|-----|---------|
| 01 Tóm tắt dự án (1.1–1.5) | **1. Tóm tắt đề tài** | Rút gọn; gộp định vị / đóng góp / phạm vi |
| 02 Tổng quan (2.1–2.2) | **2. Tổng quan** | Bỏ lặp “ECONITH là gì”; giữ bối cảnh–vấn đề–lý do |
| 03 Vấn đề nghiên cứu | **3. Vấn đề nghiên cứu** | Giữ nguyên RQ1–4, H1–4 |
| 04 Mục tiêu dự án | **3.6 Mục tiêu nghiên cứu** | Chuyển vào chương 3; bỏ lặp “không hướng tới” (chỉ còn ở 1.3) |
| 05 Kiến trúc tổng thể | **4.1, 4.5** | Gộp vào chương kiến trúc |
| 06 ECONITH World | **4.2** | Gộp |
| 07 ECONITH Quant | **4.3** | Gộp |
| 08 World ↔ Quant | **4.4** | Gộp |
| 09 Phương pháp & thực nghiệm | **5.** | Giữ B0/E1/C1, protocol, metrics |
| 10 Dữ liệu & thành phần KT | **5.5, 5.7** | Gộp vào phương pháp |
| — | **6. Kết quả thực nghiệm** | **Mới** — khung chờ số liệu / hình |
| 11 Tính sáng tạo | **7. Tính mới và đóng góp** | Rút ngắn |
| 12 Ứng dụng + 13 Giới hạn | **8. Ứng dụng và giới hạn** | Gộp một chương |
| 14 Hướng phát triển | **9.** | Rút gọn short/mid/long thành bullets |
| 15 Kết luận | **10.** | Giữ ý; rút bớt lặp |

---

## Mục đã gộp

1. **Kiến trúc (cũ 05–08) → chương 4**  
   World, Quant, Cross Impact, EventBus, luồng hoạt động nằm cùng một chương.

2. **Mục tiêu kỹ thuật (cũ 04) → 3.6**  
   Bảng T1–T7 giữ nguyên ý nghĩa (đính kèm bảng kỹ thuật).

3. **Dữ liệu + thành phần KT (cũ 10) → 5.5 / 5.7**  
   Không tách chương riêng.

4. **Ứng dụng (12) + Giới hạn (13) + ranh giới → chương 8**  
   Một lần nêu ứng dụng và giới hạn kỹ thuật.

5. **Đóng góp / tính mới (1.2 cũ + 11) → 1.2 (tóm tắt) + 7**  
   Tóm tắt ngắn ở đầu; chi tiết một lần ở chương 7.

---

## Mục đã chuyển

| Nội dung | Từ | Đến |
|----------|----|-----|
| Mục tiêu tổng quát + bảng T1–T7 | Ch. 04 | **3.6** |
| Phạm vi không thuộc mục tiêu | Ch. 04.3 | **1.3** (một lần) |
| Calibration / reproducibility notes | 9.5 | **5.6** |
| Ý nghĩa giới hạn | 13.3 | gộp vào **8.2** |

---

## Mục đã rút gọn (~40–60%)

- Lặp **“ECONITH là…” / “Hệ thống kết hợp…”** (xuất hiện ở tóm tắt, 1.1 cũ, 2.1 cũ) → **chỉ giữ một lần** ở **1. Tóm tắt** và một nhắc ngắn ở **2.4**.
- Lặp **“Không hướng tới / Không nhằm…”** (1.5, 4.3, 6.1, 12.1 cũ) → **chỉ giữ ở 1.3**.
- Phần định vị + tính mới + tính thực tiễn dài ở Ch.01 → còn mục tiêu / đóng góp / phạm vi gọn.
- Ch.14 hướng phát triển → 3 nhóm bullet.
- Ch.11 “những gì không tính sáng tạo” → một câu ở chương 7.
- Lời dẫn marketing / giới thiệu sản phẩm dài đã cắt xuống ~một nửa, ưu tiên nghiên cứu–phương pháp–khung kết quả.

---

## Nội dung được giữ (không xóa)

- RQ1–RQ4, H1–H4  
- Protocol B0 / E1 / C1, baseline, control, evaluation  
- Calibration, reproducibility, metrics  
- EventBus, World, Quant, Cross Impact, Sentinel  
- Các bảng kỹ thuật: hạn chế phương pháp, lớp kiến trúc, pipeline Quant, B0/E1/C1, metrics, thành phần KT  

---

## Chương 6 (mới)

Khung chờ chèn sau:

- Bảng B0 / E1 / C1  
- Biểu đồ so sánh, histogram, scatter, confusion matrix  
- Calibration plots, bootstrap, DM test, reproducibility  

Không điền số liệu giả trong bản tái cấu trúc này.

---

## Cách tạo lại file

```text
python KHKT_Evaluation/_gen_khkt_baocao.py
```
